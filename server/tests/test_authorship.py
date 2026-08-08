"""Rule-by-rule tests for the authorship classifier (spec §3.2/§3.3)."""
import pytest
from introspect.schema import parse_line
from introspect.schema.authorship import (
    Authorship, AuthorshipContext, ToolUseRef, classify, CHAT_KINDS,
)

MAIN = AuthorshipContext(transcript_kind="main", tool_uses={})
SIDE = AuthorshipContext(transcript_kind="subagent", tool_uses={})


def user(content, version="2.1.220", **fields):
    rec = {"type": "user", "uuid": "u1", "version": version,
           "message": {"role": "user", "content": content}}
    rec.update(fields)
    return rec


def parsed(d):
    import json
    return parse_line(json.dumps(d)).record


# (fixture_record, ctx, expected_kind, expected_detail) — order mirrors spec §3.2
CASES = [
    # 1 compaction
    (user("summary", isCompactSummary=True, promptSource="typed"), MAIN, "compact_summary", None),
    # 2 tool result — detail from ctx map, echo alone never occurs but block is authoritative
    (user([{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
     AuthorshipContext("main", {"t1": ToolUseRef("Bash", None)}), "tool_result", "Bash"),
    # 3 skill injection (this very feature's brainstorm record shape)
    (user([{"type": "text", "text": "Base directory for this skill: ..."}],
          isMeta=True, sourceToolUseID="t2"),
     AuthorshipContext("main", {"t2": ToolUseRef("Skill", "superpowers:brainstorming")}),
     "skill_injection", "superpowers:brainstorming"),
    # 4 tool injection incl. dangling ref -> null detail
    (user([{"type": "text", "text": "# Chrome automation"}], isMeta=True, sourceToolUseID="gone"),
     MAIN, "tool_injection", None),
    # 5 task notification (both signal forms)
    (user("Agent finished", origin={"kind": "task-notification"}), MAIN, "task_notification", None),
    (user("Agent finished", promptSource="system"), MAIN, "task_notification", None),
    # 6 coordinator
    (user("The coordinator sent a message", isMeta=True, origin={"kind": "coordinator"}),
     SIDE, "coordinator", None),
    # 7 human typed / queued
    (user("hello", promptSource="typed", origin={"kind": "human"}), MAIN, "human_typed", None),
    (user("also this", promptSource="queued", origin={"kind": "human"}), MAIN, "human_queued", None),
    # 7 era-boundary cluster: typed with NO origin (CLI <= 2.1.177)
    (user("wake up!", version="2.1.170", promptSource="typed"), MAIN, "human_typed", None),
    # 8 sdk automation
    (user("Review this change for security issues", promptSource="sdk"), MAIN, "sdk_automation", None),
    # 9/10 command furniture
    (user("<command-name>/model</command-name>..."), MAIN, "command_expansion", "/model"),
    (user("<local-command-stdout>Login successful</local-command-stdout>"), MAIN, "command_output", None),
    # 11 harness meta: reminder / caveat / non-string document payload
    (user("<system-reminder>Respond tersely</system-reminder>", isMeta=True), MAIN, "harness_meta", "reminder"),
    (user("<local-command-caveat>Caveat: ...</local-command-caveat>", isMeta=True), MAIN, "harness_meta", "caveat"),
    (user([{"type": "document", "source": {"data": "JVBERi0="}}], isMeta=True), MAIN, "harness_meta", None),
    # 12 interrupt markers: string + array forms, tool variant, SIDECHAIN form, pre-era form
    (user("[Request interrupted by user]"), MAIN, "interrupt_marker", None),
    (user([{"type": "text", "text": "[Request interrupted by user for tool use]"}]),
     MAIN, "interrupt_marker", "tool"),
    (user("[Request interrupted by user]", isSidechain=True), SIDE, "interrupt_marker", None),
    (user("[Request interrupted by user for tool use]", version="2.1.121"), MAIN, "interrupt_marker", "tool"),
    # 13 dispatch: sidechain opener; belt-and-suspenders (flag OR transcript kind)
    (user("You are implementing Task 3 of ...", isSidechain=True), SIDE, "dispatch", None),
    (user("You are implementing Task 3 of ..."), SIDE, "dispatch", None),  # flag lost, kind holds
    # 14 drift guard: unrecognized enum values -> unclassified, detail = the value
    (user("hi", promptSource="telepathy"), MAIN, "unclassified", "telepathy"),
    (user("hi", origin={"kind": "ouija"}), MAIN, "unclassified", "ouija"),
    # 15 pre-era inferred human (positive, version-gated)
    (user("Greetings Claude! Take your time reading", version="2.1.121"), MAIN, "human_inferred", None),
    # 16 floor: post-era fieldless MUST NOT be YOU; ancient no-version also floors
    (user("mystery text", version="2.1.220"), MAIN, "unclassified", None),
    (user("mystery text", version=None), MAIN, "unclassified", None),
]


@pytest.mark.parametrize("record,ctx,kind,detail", CASES)
def test_rule(record, ctx, kind, detail):
    got = classify(parsed(record), ctx)
    assert (got.kind, got.detail) == (kind, detail)


def test_non_user_types_are_total():
    a = parsed({"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "content": []}})
    s = parsed({"type": "system", "uuid": "s1", "subtype": "turn_duration", "durationMs": 5})
    att_h = parsed({"type": "attachment", "uuid": "at1", "attachment": {
        "type": "queued_command", "commandMode": "prompt", "origin": {"kind": "human"}, "prompt": "hi"}})
    att_f = parsed({"type": "attachment", "uuid": "at2", "attachment": {"type": "skill_listing"}})
    assert classify(a, MAIN).kind == "claude"
    assert classify(s, MAIN) == Authorship("system", "verified — record type system", "turn_duration")
    assert classify(att_h, MAIN).kind == "attachment_queued_human"
    assert classify(att_f, MAIN).kind == "attachment_furniture"
    assert classify(None, MAIN).kind == "unclassified"  # unparseable line: total, never raises


def test_you_only_via_rules_7_and_15():
    """Spec §3.2 property: no harness-shaped fixture may classify human_*."""
    for record, ctx, kind, _ in CASES:
        if kind.startswith("human_"):
            continue
        assert not classify(parsed(record), ctx).kind.startswith("human_")


def test_basis_is_verified_or_heuristic_with_reason():
    got = classify(parsed(user("hello", promptSource="typed")), MAIN)
    assert got.basis.startswith("verified — ")
    got = classify(parsed(user("Greetings", version="2.1.121")), MAIN)
    assert got.basis.startswith("heuristic — ")


def test_chat_kinds_constant_matches_spec_section_5():
    assert CHAT_KINDS == frozenset({
        "human_typed", "human_queued", "human_inferred", "claude",
        "attachment_queued_human", "interrupt_marker", "dispatch", "coordinator"})
