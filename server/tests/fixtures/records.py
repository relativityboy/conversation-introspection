"""Synthetic transcript-line builders shared by every schema/import/interpret test.

These emit records that are *shaped* like real Claude Code transcript lines (the same
envelope keys, the same block structure) but whose textual content is entirely made up.
No real transcript text ever appears here — the fixtures are deterministic-ish stand-ins
so tests never depend on a user's private session data.

Each ``make_*_line`` returns a single JSON record encoded as compact UTF-8 bytes with a
trailing newline (one ``.jsonl`` line). ``make_session_file`` concatenates them into the
byte payload of a whole session file.
"""

from __future__ import annotations

import json
import uuid as _uuidmod

# NOTE(claude): fixture default CLI version is pinned to "2.1.202" because
# test_schema_v1.test_user_record_parses_ok asserts detected_cli_version == "2.1.202".
# Bump deliberately, not incidentally.
DEFAULT_VERSION = "2.1.202"
DEFAULT_SESSION_ID = "5e551011-0000-4000-8000-000000000001"
DEFAULT_CWD = "/home/dev/synthetic-project"
DEFAULT_GIT_BRANCH = "main"
DEFAULT_TIMESTAMP = "2026-01-01T12:00:00.000Z"


def _new_uuid() -> str:
    return str(_uuidmod.uuid4())


def _short() -> str:
    return _uuidmod.uuid4().hex[:12]


def _encode(record: dict) -> bytes:
    """Serialize a record dict as one compact ``.jsonl`` line (bytes, newline-terminated)."""
    return (json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def make_pretty(line: bytes) -> bytes:
    """Re-serialize a compact builder line as pretty-printed multi-line bytes (spec §2's
    hand-edit shape): 2-space indent, one field per line, newline-terminated."""
    return (json.dumps(json.loads(line), indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _envelope(record_type: str, **overrides: object) -> dict:
    """Build a conversational envelope. ``overrides`` use the on-disk camelCase keys."""
    env = {
        "type": record_type,
        "uuid": _new_uuid(),
        "parentUuid": None,
        "sessionId": DEFAULT_SESSION_ID,
        "timestamp": DEFAULT_TIMESTAMP,
        "cwd": DEFAULT_CWD,
        "version": DEFAULT_VERSION,
        "gitBranch": DEFAULT_GIT_BRANCH,
        "isSidechain": False,
    }
    env.update(overrides)
    return env


def make_user_line(
    text: str = "synthetic user message",
    *,
    content: str | list | None = None,
    extra: dict | None = None,
    **overrides: object,
) -> bytes:
    """A ``user`` record. ``content`` overrides the default string body with a block list.

    ``extra`` injects unknown top-level keys (used to exercise forward-drift handling).
    """
    record = _envelope("user", **overrides)
    record["message"] = {"role": "user", "content": text if content is None else content}
    if extra:
        record.update(extra)
    return _encode(record)


def make_assistant_line(
    text: str = "synthetic assistant reply",
    *,
    with_thinking: bool = False,
    with_tool_use: bool = False,
    tool_use_id: str | None = None,
    model: str = "claude-opus-4-synthetic",
    usage: dict | None = None,
    extra: dict | None = None,
    message_extra: dict | None = None,
    tool_use_caller: dict | None = None,
    extra_blocks: list[dict] | None = None,
    **overrides: object,
) -> bytes:
    """An ``assistant`` record.

    Block order is deterministic: ``thinking`` (if requested) then ``text`` then
    ``tool_use`` (if requested) — matching the order the CLI persists them, which the
    ``blocks()`` extraction test asserts on.

    ``message_extra`` merges keys into the ``message`` sub-object (used to exercise
    message-level drift such as the CLI's ``type: "message"`` echo). ``tool_use_caller``
    attaches a ``caller`` payload to the ``tool_use`` block (block-level drift).
    ``extra_blocks`` are appended verbatim after the standard blocks (used to inject a
    novel content block such as the ``fallback`` model-fallback marker). ``tool_use_id``
    pins the ``tool_use`` block's ``id`` (default: random) — used by callers that need a
    dispatching block's id to match a known subagent's ``parent_tool_use_id``.
    """
    content: list[dict] = []
    if with_thinking:
        # thinking text is empty on purpose: the CLI persists the signature, never the text.
        content.append({"type": "thinking", "thinking": "", "signature": "sig_" + _short()})
    content.append({"type": "text", "text": text})
    if with_tool_use:
        tool_block = {
            "type": "tool_use",
            "id": tool_use_id or ("toolu_" + _short()),
            "name": "Bash",
            "input": {"command": "echo synthetic"},
        }
        if tool_use_caller is not None:
            tool_block["caller"] = tool_use_caller
        content.append(tool_block)
    if extra_blocks:
        content.extend(extra_blocks)
    if usage is None:
        usage = {
            "input_tokens": 12,
            "output_tokens": 34,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    record = _envelope("assistant", **overrides)
    record["message"] = {"role": "assistant", "model": model, "content": content, "usage": usage}
    if message_extra:
        record["message"].update(message_extra)
    if extra:
        record.update(extra)
    return _encode(record)


def make_tool_result_user_line(
    *,
    tool_use_id: str | None = None,
    result_text: str = "synthetic tool output",
    is_error: bool = False,
    nested: bool = True,
    extra: dict | None = None,
    **overrides: object,
) -> bytes:
    """A ``user`` record whose content is a single ``tool_result`` block.

    ``nested=True`` gives the block-array content shape the API uses for rich results;
    ``nested=False`` gives the plain-string shape. Both are valid on disk.
    """
    tuid = tool_use_id or ("toolu_" + _short())
    result_content: str | list
    if nested:
        result_content = [{"type": "text", "text": result_text}]
    else:
        result_content = result_text
    block = {
        "type": "tool_result",
        "tool_use_id": tuid,
        "content": result_content,
        "is_error": is_error,
    }
    return make_user_line(content=[block], extra=extra, **overrides)


def _thin_meta_defaults(kind: str) -> dict:
    """Field defaults for each thin-meta record kind, built fresh per call.

    Keys match the on-disk field names so every emitted field is declared on its model
    (i.e. the record parses to status "ok"). A factory (not a module constant) so
    per-record values like ``leafUuid`` are unique per call, not frozen at import.
    """
    defaults: dict[str, dict] = {
        "ai-title": {"aiTitle": "Synthetic Session Title"},
        "custom-title": {"customTitle": "My Synthetic Title"},
        "mode": {"mode": "default"},
        "permission-mode": {"permissionMode": "acceptEdits"},
        "last-prompt": {"leafUuid": _new_uuid(), "lastPrompt": "synthetic last prompt"},
        "queue-operation": {
            "operation": "enqueue",
            "content": "synthetic queued prompt",
            "timestamp": DEFAULT_TIMESTAMP,
        },
        "agent-name": {"agentName": "synthetic-agent"},
        "agent-color": {"agentColor": "yellow"},
    }
    return defaults.get(kind, {})


def make_thin_meta_line(
    kind: str,
    *,
    session_id: str = DEFAULT_SESSION_ID,
    extra: dict | None = None,
    **fields: object,
) -> bytes:
    """A thin metadata record (no conversational envelope) of the given ``kind``."""
    record: dict = {"type": kind, "sessionId": session_id}
    record.update(_thin_meta_defaults(kind))
    record.update(fields)
    if extra:
        record.update(extra)
    return _encode(record)


def make_snapshot_line(
    *,
    message_id: str | None = None,
    session_id: str = DEFAULT_SESSION_ID,
    **fields: object,
) -> bytes:
    """A minimal ``file-history-snapshot`` record.

    The bulky ``snapshot`` payload is intentionally *not* modeled by the schema (it is
    archive-only), so it lands in the record's extras — a benign info-level anomaly.
    """
    record: dict = {
        "type": "file-history-snapshot",
        "messageId": message_id or ("msg_" + _short()),
        "sessionId": session_id,
        "snapshot": {"trackedFileBackups": {}, "timestamp": DEFAULT_TIMESTAMP},
    }
    record.update(fields)
    return _encode(record)


def make_file_history_delta_line(
    *,
    message_id: str | None = None,
    session_id: str = DEFAULT_SESSION_ID,
    **fields: object,
) -> bytes:
    """A minimal ``file-history-delta`` record (sibling of ``file-history-snapshot``).

    Like the snapshot record, the bulky ``backup`` payload is archive-only and intentionally
    *not* modeled by the schema (declared opaque ``Any``); the other fields are declared, so a
    default record parses to status ``ok``. Shapes mirror the production record (verified
    read-only); no private content appears here.
    """
    record: dict = {
        "type": "file-history-delta",
        "messageId": message_id or ("msg_" + _short()),
        "snapshotMessageId": "msg_" + _short(),
        "sessionId": session_id,
        "trackingPath": "/home/dev/synthetic-project/synthetic-file.py",
        "backup": {"backupFileName": None, "version": 1, "backupTime": DEFAULT_TIMESTAMP},
        "timestamp": DEFAULT_TIMESTAMP,
    }
    record.update(fields)
    return _encode(record)


def make_system_line(
    subtype: str = "info",
    *,
    content: str | None = "synthetic system message",
    level: str | None = "info",
    extra: dict | None = None,
    **overrides: object,
) -> bytes:
    """A ``system`` record of the given ``subtype`` (full conversational envelope).

    Pass ``content=None`` / ``level=None`` to omit the key — in the wild,
    ``turn_duration`` records carry neither and ``api_error`` carries only ``level``.
    Subtype-specific payload fields (durationMs, hookCount, ...) go via ``overrides``.
    """
    record = _envelope("system", **overrides)
    record["subtype"] = subtype
    if level is not None:
        record["level"] = level
    if content is not None:
        record["content"] = content
    if extra:
        record.update(extra)
    return _encode(record)


def make_attachment_line(
    *,
    attachment: dict | None = None,
    extra: dict | None = None,
    **overrides: object,
) -> bytes:
    """An ``attachment`` record: a full conversational envelope carrying an ``attachment``
    payload (an opaque, structured blob the schema preserves but never interprets)."""
    record = _envelope("attachment", **overrides)
    record["attachment"] = (
        attachment
        if attachment is not None
        else {"type": "deferred_tools_delta", "addedNames": [], "removedNames": []}
    )
    if extra:
        record.update(extra)
    return _encode(record)


def make_queued_command_line(
    *,
    prompt: str = "synthetic queued human prompt about a lone cormorant",
    human: bool = True,
    extra: dict | None = None,
    **overrides: object,
) -> bytes:
    """An ``attachment`` record whose body is a ``queued_command`` (Task P4-F1).

    ``human=True`` builds the human-origin shape the interpreter rescues into one text block:
    ``commandMode == "prompt"`` + ``origin.kind == "human"``. ``human=False`` builds the
    harness-furniture variant (``commandMode == "task-notification"``, no ``origin``) that must
    stay zero-block even though it, too, carries a ``prompt`` key — so all three discriminator
    conditions are load-bearing. The key paths (``attachment.type`` / ``commandMode`` /
    ``origin.kind`` / ``prompt``) mirror the two real production records, verified read-only;
    the prompt TEXT here is entirely invented (no private words in fixtures, spec §11).
    """
    if human:
        attachment = {
            "type": "queued_command",
            "prompt": prompt,
            "commandMode": "prompt",
            "origin": {"kind": "human"},
            "timestamp": DEFAULT_TIMESTAMP,
        }
    else:
        attachment = {
            "type": "queued_command",
            "prompt": prompt,
            "commandMode": "task-notification",
        }
    return make_attachment_line(attachment=attachment, extra=extra, **overrides)


def make_session_file(lines: list[bytes]) -> bytes:
    """Concatenate record lines (each already newline-terminated) into a session payload."""
    return b"".join(lines)
