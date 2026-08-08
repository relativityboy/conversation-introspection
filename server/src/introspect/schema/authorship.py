"""Authorship classification (spec 2026-08-07 §3): who really authored each record.

Pure and DB-free by the same binding tenet as v1.py — all context (transcript kind,
tool_use map) arrives injected via AuthorshipContext. Total over every record type and
None; never raises. First-match rule order IS the contract; renumbering requires a spec
change.
"""
from __future__ import annotations

from dataclasses import dataclass

from introspect.schema.v1 import (
    AssistantRecord, AttachmentRecord, BaseRecord, SystemRecord, TextBlock,
    ToolResultBlock, UserRecord,
)

PROMPT_SOURCE_ERA = (2, 1, 168)  # first CLI version that stamps promptSource (verified 2026-08-07)
KNOWN_PROMPT_SOURCES = frozenset({"typed", "queued", "system", "sdk"})
KNOWN_ORIGIN_KINDS = frozenset({"human", "task-notification", "coordinator"})
INTERRUPT_PREFIX = "[Request interrupted by user"
CHAT_KINDS = frozenset({
    "human_typed", "human_queued", "human_inferred", "claude",
    "attachment_queued_human", "interrupt_marker", "dispatch", "coordinator",
})


@dataclass(frozen=True)
class ToolUseRef:
    name: str
    skill: str | None  # input["skill"] when name == "Skill", else None


@dataclass(frozen=True)
class AuthorshipContext:
    transcript_kind: str  # "main" | "subagent"
    tool_uses: dict[str, ToolUseRef]  # every tool_use in the record's transcript


@dataclass(frozen=True)
class Authorship:
    kind: str
    basis: str
    detail: str | None


def _version_tuple(version: str | None) -> tuple[int, ...] | None:
    if not version:
        return None
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def _first_text(record: UserRecord) -> str | None:
    content = record.message.content
    if isinstance(content, str):
        return content
    for block in content:
        if isinstance(block, TextBlock):
            return block.text
    return None


def _has_text_or_image(record: UserRecord) -> bool:
    content = record.message.content
    if isinstance(content, str):
        return True
    return any(
        isinstance(b, TextBlock) or getattr(b, "type", None) == "image" for b in content
    )


def _tool_result_id(record: UserRecord) -> str | None:
    content = record.message.content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, ToolResultBlock):
                return block.tool_use_id
    return None


def _origin_kind(record: UserRecord) -> str | None:
    origin = record.origin
    if isinstance(origin, dict):
        kind = origin.get("kind")
        return kind if isinstance(kind, str) else None
    return None


def _command_name(text: str) -> str | None:
    start = text.find("<command-name>")
    if start < 0:
        return None
    end = text.find("</command-name>", start)
    return text[start + len("<command-name>"): end].strip() if end > start else None


def _classify_user(record: UserRecord, ctx: AuthorshipContext) -> Authorship:
    text = _first_text(record)
    origin_kind = _origin_kind(record)
    ps = record.promptSource

    # 1 — compaction summary
    if record.isCompactSummary:
        return Authorship("compact_summary", "verified — isCompactSummary", None)
    # 2 — tool result (block authoritative; toolUseResult echo never occurs alone)
    tr_id = _tool_result_id(record)
    if tr_id is not None or record.toolUseResult is not None:
        ref = ctx.tool_uses.get(tr_id) if tr_id else None
        return Authorship("tool_result", "verified — tool_result block", ref.name if ref else None)
    # 3/4 — injected content, kind decided by the referenced tool
    if record.sourceToolUseID is not None:
        ref = ctx.tool_uses.get(record.sourceToolUseID)
        if ref is not None and ref.name == "Skill":
            return Authorship("skill_injection", "verified — sourceToolUseID → Skill", ref.skill)
        return Authorship(
            "tool_injection", "verified — sourceToolUseID", ref.name if ref else None)
    # 5 — task notification (either signal)
    if origin_kind == "task-notification" or ps == "system":
        return Authorship("task_notification", "verified — origin/promptSource", None)
    # 6 — coordinator relay
    if origin_kind == "coordinator":
        return Authorship("coordinator", "verified — origin.kind coordinator", None)
    # 7 — the human, explicitly stamped
    if ps == "typed":
        return Authorship("human_typed", "verified — promptSource: typed", None)
    if ps == "queued":
        return Authorship("human_queued", "verified — promptSource: queued", None)
    # 8 — programmatic callers
    if ps == "sdk":
        return Authorship("sdk_automation", "verified — promptSource: sdk", None)
    # 9/10 — slash-command furniture
    if isinstance(text, str) and text.startswith("<command-name>"):
        return Authorship("command_expansion", "heuristic — <command-name> prefix", _command_name(text))
    if isinstance(text, str) and text.startswith("<local-command-stdout>"):
        return Authorship("command_output", "heuristic — <local-command-stdout> prefix", None)
    # 11 — remaining CLI-internal records (also the home for non-string meta payloads)
    if record.isMeta:
        detail = None
        if isinstance(text, str):
            if text.startswith("<system-reminder>"):
                detail = "reminder"
            elif text.startswith("<local-command-caveat>"):
                detail = "caveat"
        return Authorship("harness_meta", "heuristic — isMeta", detail)
    # 12 — interruption markers, BEFORE the sidechain rule (spec §2: 4 sidechain instances)
    if isinstance(text, str) and text.startswith(INTERRUPT_PREFIX):
        detail = "tool" if text.startswith(INTERRUPT_PREFIX + " for tool use") else None
        return Authorship("interrupt_marker", "heuristic — interrupt marker text", detail)
    # 13 — dispatcher-authored records in subagent transcripts (flag OR transcript kind:
    # a hand-edited record that lost isSidechain must not cross to human — spec §3.2)
    if record.isSidechain or ctx.transcript_kind == "subagent":
        return Authorship("dispatch", "verified — sidechain transcript", None)
    # 14 — drift guard: recognizable fields carrying unrecognized values -> alarm, never YOU
    if ps is not None and ps not in KNOWN_PROMPT_SOURCES:
        return Authorship("unclassified", "verified — unrecognized promptSource", ps)
    if origin_kind is not None and origin_kind not in KNOWN_ORIGIN_KINDS:
        return Authorship("unclassified", "verified — unrecognized origin.kind", origin_kind)
    # 15 — pre-promptSource-era inference (POSITIVE, version-gated: from 2.1.168 every human
    # prompt is stamped, so a post-era fieldless record is evidence of harness origin)
    version = _version_tuple(record.version)
    if (
        ctx.transcript_kind == "main"
        and not record.isSidechain
        and version is not None
        and version < PROMPT_SOURCE_ERA
        and _has_text_or_image(record)
    ):
        return Authorship(
            "human_inferred",
            "heuristic — pre-2.1.168 record, main transcript, no harness markers", None)
    # 16 — the honest floor / drift alarm
    return Authorship("unclassified", "heuristic — no rule matched", None)


def classify(record: BaseRecord | None, ctx: AuthorshipContext) -> Authorship:
    if record is None:
        return Authorship("unclassified", "heuristic — unparseable record", None)
    if isinstance(record, UserRecord):
        return _classify_user(record, ctx)
    if isinstance(record, AssistantRecord):
        return Authorship("claude", "verified — record type assistant", None)
    if isinstance(record, SystemRecord):
        return Authorship("system", "verified — record type system", record.subtype)
    if isinstance(record, AttachmentRecord):
        if record.blocks():
            return Authorship(
                "attachment_queued_human", "verified — rescued human queued_command", None)
        return Authorship("attachment_furniture", "verified — zero-block attachment", None)
    return Authorship("unclassified", "heuristic — unregistered record type", None)
