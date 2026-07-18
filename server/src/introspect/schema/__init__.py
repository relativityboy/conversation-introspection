"""Tolerant line-parsing entry point for Claude Code transcript records.

Public surface:

* :data:`SCHEMA_VERSION` — identifies this schema generation for provenance stamping.
* :data:`REGISTRY` — the ``type`` string → model class table.
* :func:`parse_line` — the only function importers/interpreters call; it never raises.
* The dataclasses :class:`ParseResult`, :class:`Anomaly`, :class:`NormalizedBlock`.

This package is intentionally free of any dependency on :mod:`introspect.db` /
:mod:`introspect.models`: parsing what a record *means* is separate from how it is stored.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from introspect.schema.v1 import (
    SCHEMA_VERSION,
    AgentColorRecord,
    AgentNameRecord,
    Anomaly,
    AssistantRecord,
    AttachmentRecord,
    AiTitleRecord,
    BaseRecord,
    CustomTitleRecord,
    FileHistorySnapshotRecord,
    LastPromptRecord,
    ModeRecord,
    NormalizedBlock,
    ParseResult,
    PermissionModeRecord,
    QueueOperationRecord,
    SystemRecord,
    UserRecord,
    collect_extra_fields,
)

__all__ = [
    "SCHEMA_VERSION",
    "REGISTRY",
    "parse_line",
    "ParseResult",
    "Anomaly",
    "NormalizedBlock",
    "BaseRecord",
]

REGISTRY: dict[str, type[BaseModel]] = {
    "user": UserRecord,
    "assistant": AssistantRecord,
    "system": SystemRecord,
    "attachment": AttachmentRecord,
    "ai-title": AiTitleRecord,
    "custom-title": CustomTitleRecord,
    "mode": ModeRecord,
    "permission-mode": PermissionModeRecord,
    "last-prompt": LastPromptRecord,
    "queue-operation": QueueOperationRecord,
    "agent-name": AgentNameRecord,
    "agent-color": AgentColorRecord,
    "file-history-snapshot": FileHistorySnapshotRecord,
}


def _validation_detail(exc: ValidationError) -> dict:
    """A JSON-serializable summary of a pydantic ValidationError (no raw input echoed)."""
    return {
        "errors": [
            {
                "loc": [str(part) for part in err.get("loc", ())],
                "type": err.get("type", ""),
                "msg": err.get("msg", ""),
            }
            for err in exc.errors(include_url=False)
        ]
    }


def parse_line(raw: bytes) -> ParseResult:
    """Parse one transcript ``.jsonl`` line into a :class:`ParseResult`. Never raises.

    Branch order (binding per the task brief):

    1. ``json.loads`` — failure → ``invalid_json`` error anomaly, status ``anomaly``.
    2. type lookup — unknown type → ``unknown_record_type`` warn anomaly, status
       ``partial``, ``record`` is None (a non-object JSON has no type and lands here).
    3. ``model_validate`` — :class:`ValidationError` → ``validation_error`` error anomaly,
       status ``anomaly``.
    4. recursive extra fields present → one ``unknown_field`` info anomaly (field names in
       detail), status ``partial``.
    5. otherwise status ``ok``.
    """
    # 1. JSON decode.
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        anomaly = Anomaly("error", "invalid_json", {"error": str(exc)})
        return ParseResult(None, None, None, None, "anomaly", [anomaly])

    record_type = data.get("type") if isinstance(data, dict) else None

    # 2. Type lookup.
    model_cls = REGISTRY.get(record_type) if record_type is not None else None
    if model_cls is None:
        anomaly = Anomaly("warn", "unknown_record_type", {"type": record_type})
        return ParseResult(None, record_type, None, None, "partial", [anomaly])

    # 3. Structural validation.
    try:
        record = model_cls.model_validate(data)
    except ValidationError as exc:
        anomaly = Anomaly("error", "validation_error", _validation_detail(exc))
        return ParseResult(None, record_type, None, None, "anomaly", [anomaly])

    record_uuid = getattr(record, "uuid", None)
    detected_cli_version = getattr(record, "version", None)

    # 4. Forward-drift: unknown extra fields anywhere in the validated tree.
    extra_fields = collect_extra_fields(record)
    if extra_fields:
        anomaly = Anomaly("info", "unknown_field", {"fields": extra_fields})
        return ParseResult(
            record, record_type, record_uuid, detected_cli_version, "partial", [anomaly]
        )

    # 5. Clean parse.
    return ParseResult(record, record_type, record_uuid, detected_cli_version, "ok", [])
