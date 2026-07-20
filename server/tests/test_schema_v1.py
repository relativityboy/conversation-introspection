import json

from introspect.schema import REGISTRY, SCHEMA_VERSION, parse_line
from introspect.schema.v1 import BaseRecord
from tests.fixtures.records import (
    make_assistant_line,
    make_attachment_line,
    make_file_history_delta_line,
    make_session_file,
    make_snapshot_line,
    make_system_line,
    make_thin_meta_line,
    make_tool_result_user_line,
    make_user_line,
)


# --- Required representative set (verbatim from task-3 brief) ---------------------------


def test_user_record_parses_ok():
    r = parse_line(make_user_line(text="hello world"))
    assert r.status == "ok" and r.record_type == "user" and r.record_uuid
    assert r.detected_cli_version == "2.1.202"


def test_assistant_blocks_extracted():
    r = parse_line(make_assistant_line(text="hi", with_thinking=True, with_tool_use=True))
    kinds = [b.kind for b in r.record.blocks()]
    assert kinds == ["thinking", "text", "tool_use"]


def test_unknown_extra_field_is_info_partial():
    line = make_user_line(extra={"futureField": 1})
    r = parse_line(line)
    assert r.status == "partial"
    assert [a.severity for a in r.anomalies] == ["info"]


def test_unknown_record_type_is_warn():
    r = parse_line(json.dumps({"type": "hologram", "sessionId": "s"}).encode())
    assert r.status == "partial" and r.record is None
    assert r.anomalies[0].kind == "unknown_record_type"


def test_invalid_json_is_error():
    r = parse_line(b"{not json")
    assert r.status == "anomaly" and r.anomalies[0].kind == "invalid_json"


def test_malformed_known_type_is_error():
    r = parse_line(json.dumps({"type": "assistant", "message": 42}).encode())
    assert r.status == "anomaly" and r.anomalies[0].kind == "validation_error"


def test_thin_meta_records_parse():
    from tests.fixtures.records import make_thin_meta_line

    for kind in [
        "ai-title",
        "custom-title",
        "mode",
        "permission-mode",
        "last-prompt",
        "queue-operation",
        "agent-name",
    ]:
        assert parse_line(make_thin_meta_line(kind)).status == "ok", kind


# --- Self-review coverage (extras recursion, nested tool_result, registry round-trip) ---


def test_schema_version_constant():
    assert SCHEMA_VERSION == "introspect-schema/4"


def test_anomaly_severity_is_warn_for_unknown_type():
    r = parse_line(json.dumps({"type": "hologram", "sessionId": "s"}).encode())
    assert r.anomalies[0].severity == "warn"
    assert r.anomalies[0].detail["type"] == "hologram"
    assert r.record_type == "hologram"


def test_unknown_field_detail_lists_field_names():
    r = parse_line(make_user_line(extra={"futureField": 1, "anotherOne": 2}))
    assert r.status == "partial"
    assert set(r.anomalies[0].detail["fields"]) == {"futureField", "anotherOne"}


def test_extras_collected_recursively_from_nested_message():
    # A future field nested inside the message sub-object must still be surfaced.
    line = json.dumps(
        {"type": "user", "sessionId": "s", "message": {"role": "user", "content": "hi", "surprise": 7}}
    ).encode()
    r = parse_line(line)
    assert r.status == "partial"
    assert r.anomalies[0].kind == "unknown_field"
    assert "surprise" in r.anomalies[0].detail["fields"]


def test_thinking_block_text_is_empty_but_signature_kept():
    r = parse_line(make_assistant_line(with_thinking=True))
    thinking = r.record.blocks()[0]
    assert thinking.kind == "thinking" and thinking.text == ""
    assert thinking.payload and thinking.payload.get("signature")


def test_tool_use_block_normalization():
    r = parse_line(make_assistant_line(with_tool_use=True))
    tool = [b for b in r.record.blocks() if b.kind == "tool_use"][0]
    assert tool.tool_name == "Bash" and tool.tool_use_id.startswith("toolu_")
    assert tool.payload == {"command": "echo synthetic"}


def test_nested_tool_result_content_is_flattened_and_preserved():
    r = parse_line(make_tool_result_user_line(result_text="the answer", nested=True))
    blocks = r.record.blocks()
    assert len(blocks) == 1
    tr = blocks[0]
    assert tr.kind == "tool_result" and tr.is_error is False
    assert tr.text == "the answer"  # flattened from nested [{"type":"text",...}]
    assert tr.payload["content"] == [{"type": "text", "text": "the answer"}]


def test_string_tool_result_content():
    r = parse_line(make_tool_result_user_line(result_text="plain", nested=False))
    tr = r.record.blocks()[0]
    assert tr.kind == "tool_result" and tr.text == "plain" and tr.payload is None


def test_unknown_block_type_falls_back_but_is_still_a_block():
    line = make_user_line(
        content=[{"type": "future_block", "data": {"x": 1}}],
    )
    r = parse_line(line)
    # The block validates via the UnknownBlock fallback and appears in blocks() with its
    # type; its undeclared keys ("data") are forward drift, so the parse is partial.
    assert r.status == "partial"
    assert r.anomalies[0].kind == "unknown_field"
    block = r.record.blocks()[0]
    assert block.kind == "future_block"
    assert block.payload["type"] == "future_block"


def test_string_user_content_becomes_single_text_block():
    r = parse_line(make_user_line(text="just a string"))
    blocks = r.record.blocks()
    assert [b.kind for b in blocks] == ["text"]
    assert blocks[0].text == "just a string"


def test_snapshot_record_parses_ok_with_opaque_payload():
    r = parse_line(make_snapshot_line())
    assert r.record is not None and r.record_type == "file-history-snapshot"
    # snapshot is a declared *opaque* field: drift-we-decided-never-to-interpret is not
    # an anomaly. The payload is preserved verbatim on the record, never recursed.
    assert r.status == "ok" and r.anomalies == []
    assert r.record.snapshot == {"trackedFileBackups": {}, "timestamp": "2026-01-01T12:00:00.000Z"}


def test_survey_documented_fields_parse_ok():
    # A user line carrying the spec-survey fields must be a clean parse, zero anomalies.
    r = parse_line(
        make_user_line(
            userType="external",
            permissionMode="default",
            promptId="prompt_synthetic_1",
            promptSource="terminal",
            toolUseResult={"stdout": "synthetic", "interrupted": False},
        )
    )
    assert r.status == "ok" and r.anomalies == []

    # An assistant line with requestId + full survey-known usage must also parse clean.
    usage = {
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_creation_input_tokens": 1,
        "cache_read_input_tokens": 2,
        "cache_creation": {"ephemeral_5m_input_tokens": 1, "ephemeral_1h_input_tokens": 0},
        "server_tool_use": {"web_search_requests": 0},
        "service_tier": "standard",
        "speed": "standard",
        "iterations": [],
        "inference_geo": "not_available",
    }
    r2 = parse_line(make_assistant_line(requestId="req_synthetic", usage=usage))
    assert r2.status == "ok" and r2.anomalies == []


def test_meta_record_blocks_is_empty():
    r = parse_line(make_thin_meta_line("mode"))
    assert r.record.blocks() == []


def test_every_registry_type_instantiates_and_round_trips():
    lines = {
        "user": make_user_line(),
        "assistant": make_assistant_line(),
        "system": json.dumps({"type": "system", "sessionId": "s", "subtype": "init"}).encode(),
        "attachment": json.dumps({"type": "attachment", "sessionId": "s"}).encode(),
        "ai-title": make_thin_meta_line("ai-title"),
        "custom-title": make_thin_meta_line("custom-title"),
        "mode": make_thin_meta_line("mode"),
        "permission-mode": make_thin_meta_line("permission-mode"),
        "last-prompt": make_thin_meta_line("last-prompt"),
        "queue-operation": make_thin_meta_line("queue-operation"),
        "agent-name": make_thin_meta_line("agent-name"),
        "agent-color": make_thin_meta_line("agent-color"),
        "file-history-snapshot": make_snapshot_line(),
        "file-history-delta": make_file_history_delta_line(),
    }
    assert set(lines) == set(REGISTRY)
    for rtype, line in lines.items():
        r = parse_line(line)
        assert r.record is not None, rtype
        assert isinstance(r.record, BaseRecord), rtype
        assert isinstance(r.record, REGISTRY[rtype]), rtype
        assert r.record_type == rtype, rtype
        # model round-trips through JSON without loss of the type discriminator.
        assert json.loads(r.record.model_dump_json())["type"] == rtype, rtype


def test_non_object_json_is_unknown_record_type():
    r = parse_line(b"42")
    assert r.record is None and r.status == "partial"
    assert r.anomalies[0].kind == "unknown_record_type"


def test_make_session_file_concatenates_lines():
    payload = make_session_file([make_user_line(text="a"), make_assistant_line(text="b")])
    parsed = [parse_line(line.encode()) for line in payload.decode().splitlines()]
    assert [p.record_type for p in parsed] == ["user", "assistant"]


# --- Schema v2: production-drift fields declared at their verified locations --------------
# Each test carries one newly-declared field at its real position and asserts a clean parse
# (status "ok", zero anomalies). Field placements were confirmed against the first
# production import (see task-schema2-report.md).


def test_envelope_agent_id_and_slug_parse_ok():
    # agentId + slug are envelope-level (seen on user/assistant/attachment records).
    r = parse_line(make_user_line(extra={"agentId": "a00baa863322de8ee", "slug": "abstract-nova"}))
    assert r.status == "ok" and r.anomalies == []


def test_assistant_attribution_fields_parse_ok():
    r = parse_line(
        make_assistant_line(
            extra={
                "attributionAgent": "general-purpose",
                "attributionSkill": "superpowers:writing-plans",
                "attributionPlugin": "superpowers",
            }
        )
    )
    assert r.status == "ok" and r.anomalies == []


def test_user_source_tool_assistant_uuid_parses_ok():
    r = parse_line(
        make_user_line(extra={"sourceToolAssistantUUID": "ade093d4-b206-452a-a4b9-43ec41cfaf8e"})
    )
    assert r.status == "ok" and r.anomalies == []


def test_assistant_message_level_type_parses_ok():
    # The CLI echoes a message-level `type: "message"` on the assistant message object.
    r = parse_line(make_assistant_line(message_extra={"type": "message"}))
    assert r.status == "ok" and r.anomalies == []


def test_tool_use_block_caller_parses_ok():
    # `caller` rides on the tool_use block; its structured interior is opaque, not recursed.
    r = parse_line(make_assistant_line(with_tool_use=True, tool_use_caller={"type": "direct"}))
    assert r.status == "ok" and r.anomalies == []


def test_last_prompt_field_parses_ok():
    r = parse_line(make_thin_meta_line("last-prompt"))
    assert r.status == "ok" and r.anomalies == []
    assert r.record.lastPrompt == "synthetic last prompt"


def test_attachment_record_opaque_payload_parses_ok():
    r = parse_line(make_attachment_line())
    assert r.record_type == "attachment"
    assert r.status == "ok" and r.anomalies == []
    assert r.record.attachment["type"] == "deferred_tools_delta"


# --- Schema v3: human-origin queued_command attachments become one text block (Task P4-F1) --
# The two real production records are queued_command payloads with commandMode "prompt" and
# origin.kind "human": verbatim human turns whose only home in the DAG is the attachment record.
# The interpreter rescues them into ONE text block; every other attachment shape stays silent.


def test_human_queued_command_yields_one_text_block():
    from tests.fixtures.records import make_queued_command_line

    r = parse_line(make_queued_command_line(prompt="rescue this queued human turn"))
    # Still a clean parse — the attachment body stays opaque for anomaly purposes.
    assert r.record_type == "attachment"
    assert r.status == "ok" and r.anomalies == []
    blocks = r.record.blocks()
    assert len(blocks) == 1
    assert blocks[0].kind == "text"
    assert blocks[0].text == "rescue this queued human turn"


def test_furniture_queued_command_yields_zero_blocks():
    # The non-human "task-notification" variant ALSO carries a `prompt` key, but is harness
    # furniture: commandMode != "prompt" and no origin.kind == "human", so it stays zero-block.
    from tests.fixtures.records import make_queued_command_line

    r = parse_line(make_queued_command_line(human=False, prompt="furniture, not a human turn"))
    assert r.status == "ok" and r.anomalies == []
    assert r.record.blocks() == []


def test_other_attachment_types_yield_zero_blocks():
    # deferred_tools_delta (and every non-queued_command attachment) keeps yielding nothing.
    r = parse_line(make_attachment_line())
    assert r.record.blocks() == []


def test_queued_command_without_human_origin_yields_zero_blocks():
    # commandMode "prompt" but a non-human origin (e.g. a coordinator-issued prompt) is not a
    # human turn — the origin.kind guard is load-bearing, not just the commandMode.
    line = make_attachment_line(
        attachment={
            "type": "queued_command",
            "prompt": "issued by a coordinator, not typed by a human",
            "commandMode": "prompt",
            "origin": {"kind": "coordinator"},
        }
    )
    r = parse_line(line)
    assert r.record.blocks() == []


def test_agent_color_record_parses_ok():
    r = parse_line(make_thin_meta_line("agent-color"))
    assert r.record is not None and r.record_type == "agent-color"
    assert r.status == "ok" and r.anomalies == []
    assert r.record.agentColor == "yellow"


# --- Schema v2, residual family: system subtype payloads + stragglers ---------------------
# Positions verified against the post-reparse residual (1,150 anomalies); see report.


def test_system_turn_duration_fields_parse_ok():
    r = parse_line(
        make_system_line(
            "turn_duration",
            content=None,
            level=None,
            durationMs=75779,
            messageCount=42,
            pendingBackgroundAgentCount=1,
            isMeta=False,
        )
    )
    assert r.status == "ok" and r.anomalies == []


def test_system_stop_hook_summary_fields_parse_ok():
    r = parse_line(
        make_system_line(
            "stop_hook_summary",
            content=None,
            hookCount=1,
            hookInfos=[{"command": "synthetic-hook.sh"}],
            hookErrors=[],
            hookAdditionalContext=[],
            preventedContinuation=False,
            stopReason="",
            hasOutput=False,
            toolUseID="28aba9c1-b824-4698-b690-7189ccb6ddef",
        )
    )
    assert r.status == "ok" and r.anomalies == []


def test_system_api_error_fields_parse_ok():
    # `error` and `cause` are structured payloads (opaque Any): their interiors must not
    # be recursed for extras, so nested unknown keys cause no anomalies.
    r = parse_line(
        make_system_line(
            "api_error",
            content=None,
            error={"status": 529, "error": {"type": "overloaded_error"}},
            cause={"code": "ECONNRESET", "path": "https://synthetic.example/v1"},
            retryInMs=596.298,
            retryAttempt=1,
            maxRetries=10,
        )
    )
    assert r.status == "ok" and r.anomalies == []


def test_envelope_is_meta_and_snake_session_id_parse_ok():
    # isMeta was verified on user AND system records; session_id (snake_case sibling of
    # sessionId) on all four envelope families -> both live on Envelope.
    r = parse_line(
        make_user_line(extra={"isMeta": True, "session_id": "1a501b88-afd6-4331-bfca-f173e8bf513d"})
    )
    assert r.status == "ok" and r.anomalies == []
    r2 = parse_line(
        make_system_line("away_summary", isMeta=True, session_id="1a501b88-afd6-4331-bfca-f173e8bf513d")
    )
    assert r2.status == "ok" and r2.anomalies == []


def test_assistant_mcp_attribution_and_error_parse_ok():
    # attributionMcp* sit beside the other attribution fields; assistant-level `error`
    # is a plain string in the wild (unlike the system api_error dict).
    r = parse_line(
        make_assistant_line(
            extra={
                "attributionMcpServer": "plugin:playwright:playwright",
                "attributionMcpTool": "browser_navigate",
                "error": "server_error",
            }
        )
    )
    assert r.status == "ok" and r.anomalies == []


def test_user_origin_and_source_tool_use_id_parse_ok():
    # `origin` is a structured payload (opaque Any); sourceToolUseID sits beside
    # sourceToolAssistantUUID on user records.
    r = parse_line(
        make_user_line(
            extra={
                "origin": {"kind": "coordinator"},
                "sourceToolUseID": "toolu_synthetic0001",
            }
        )
    )
    assert r.status == "ok" and r.anomalies == []


def test_full_production_drift_line_parses_clean():
    # A realistic assistant line carrying every v2 drift field at once must parse with no
    # anomalies — the whole point of the schema/2 bump.
    r = parse_line(
        make_assistant_line(
            with_thinking=True,
            with_tool_use=True,
            tool_use_caller={"type": "direct"},
            message_extra={"type": "message"},
            extra={
                "agentId": "a00baa863322de8ee",
                "slug": "abstract-nova",
                "attributionAgent": "general-purpose",
                "attributionSkill": "superpowers:writing-plans",
                "attributionPlugin": "superpowers",
            },
        )
    )
    assert r.status == "ok" and r.anomalies == []


# --- Schema v4: second production-drift pass (new CLI versions ~2.1.207-2.1.215) ----------
# Positions verified read-only against the post-import floor (3,586 info + 10 warn); see
# task-p4-f7-report.md. Each test carries the newly-declared field(s) at the real location
# and asserts a clean parse (status "ok", zero anomalies).


def test_assistant_effort_and_api_error_fields_parse_ok():
    # `effort` (reasoning effort, dominant new field) + the api-error markers + supersedes
    # list are assistant-record top-level.
    r = parse_line(
        make_assistant_line(
            extra={
                "effort": "xhigh",
                "isApiErrorMessage": True,
                "apiErrorStatus": 529,
                "supersedesUuids": ["05fe2f3c-94f8-4007-b3bb-9280aa41bc22"],
            }
        )
    )
    assert r.status == "ok" and r.anomalies == []


def test_assistant_message_container_and_context_management_parse_ok():
    # `container` and `context_management` ride on the assistant MESSAGE object (null in the
    # wild; declared opaque Any so a future populated shape never becomes a validation error).
    r = parse_line(
        make_assistant_line(message_extra={"container": None, "context_management": None})
    )
    assert r.status == "ok" and r.anomalies == []


def test_model_fallback_block_parses_ok():
    # A new content block `{"type":"fallback","from":{...},"to":{...}}` records a model
    # fallback inline in an assistant turn. `from`/`to` are opaque (aliased; `from` is a
    # Python keyword) so the block parses clean and yields a normalized "fallback" block.
    r = parse_line(
        make_assistant_line(
            extra_blocks=[
                {
                    "type": "fallback",
                    "from": {"model": "claude-opus-4"},
                    "to": {"model": "claude-sonnet-4"},
                }
            ]
        )
    )
    assert r.status == "ok" and r.anomalies == []
    fallback = [b for b in r.record.blocks() if b.kind == "fallback"]
    assert len(fallback) == 1
    assert fallback[0].payload["from"] == {"model": "claude-opus-4"}


def test_user_tool_denial_and_interrupt_fields_parse_ok():
    # toolEndsTurn / interruptedMessageId / toolDenialKind / classifierMetaLines are all
    # user-record top-level in the drift population.
    r = parse_line(
        make_user_line(
            extra={
                "toolEndsTurn": True,
                "interruptedMessageId": "msg_synthetic0000000000000000",
                "toolDenialKind": "user-rejected",
                "classifierMetaLines": "synthetic-classifier-meta-line-token",
            }
        )
    )
    assert r.status == "ok" and r.anomalies == []


def test_system_model_refusal_fallback_fields_parse_ok():
    # The new system subtype "model_refusal_fallback" carries a whole field family; all land
    # on SystemRecord (envelope-derived). `retractedMessageUuids` is opaque Any (a list).
    r = parse_line(
        make_system_line(
            "model_refusal_fallback",
            content=None,
            trigger="refusal",
            direction="retry",
            originalModel="claude-opus-4-x",
            fallbackModel="claude-sonnet-4-x",
            requestId="req_synthetic0000000000000000",
            apiRefusalCategory="cyber",
            apiRefusalExplanation=None,
            retractedMessageUuids=["05fe2f3c-94f8-4007-b3bb-9280aa41bc22"],
            refusedUserMessageUuid="c67b666f-80b3-4a5d-8c85-d6bfb75decfc",
        )
    )
    assert r.status == "ok" and r.anomalies == []


def test_file_history_delta_record_parses_ok():
    # A new record type sibling to file-history-snapshot; `backup` is opaque Any (archive-only).
    r = parse_line(make_file_history_delta_line())
    assert r.record is not None and r.record_type == "file-history-delta"
    assert r.status == "ok" and r.anomalies == []
    assert r.record.blocks() == []
