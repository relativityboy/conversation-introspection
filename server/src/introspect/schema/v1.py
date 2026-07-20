"""Schema registry v1: tolerant Pydantic models for Claude Code transcript records.

This module is the *interpretation heart* of the archive. It is deliberately DB-free —
nothing here imports :mod:`introspect.db` or :mod:`introspect.models` — so the shape of a
record and the shape of a storage row can evolve independently.

Design tenets (binding, see task-3-brief.md):

* **Never raise.** Callers use :func:`introspect.schema.parse_line`, which converts every
  failure mode (bad JSON, unknown type, validation error) into a :class:`ParseResult`
  carrying :class:`Anomaly` records. Malformed data is data, not an exception.
* **Forward-drift is normal.** Every model sets ``extra="allow"``. Unknown *extra* fields
  are surfaced as info-level anomalies, never errors — a newer CLI adding a field must not
  break ingestion of a whole line.
* **``blocks()`` is the one interface downstream sees.** The later interpreter consumes
  :class:`NormalizedBlock` lists only; it never touches the raw pydantic tree. Non-
  conversational records return an empty list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag

# NOTE(claude): the module name (v1) is code organization; this constant tracks the
# registry GENERATION stamped on parsed rows. They diverge by design — bumping the
# generation does not require forking a new module.
SCHEMA_VERSION = "introspect-schema/3"


# --- Normalized output shape ------------------------------------------------------------


@dataclass
class NormalizedBlock:
    """The single, storage-agnostic block shape the interpreter consumes.

    Every conversational content block (text / thinking / tool_use / tool_result / unknown)
    collapses to this. ``payload`` carries block-kind-specific structured data that has no
    dedicated column (e.g. a tool_use ``input`` dict, a thinking ``signature``).
    """

    kind: str
    text: str | None
    tool_name: str | None
    tool_use_id: str | None
    is_error: bool | None
    payload: dict | None


@dataclass
class Anomaly:
    severity: str  # 'info' | 'warn' | 'error'
    kind: str  # 'unknown_field' | 'unknown_record_type' | 'validation_error' | 'invalid_json'
    detail: dict


@dataclass
class ParseResult:
    record: BaseRecord | None  # validated model, None if unparseable/unknown
    record_type: str | None
    record_uuid: str | None
    detected_cli_version: str | None
    status: str  # 'ok' | 'partial' (info/warn anomalies) | 'anomaly' (error)
    anomalies: list[Anomaly]


# --- Content blocks (discriminated by ``type`` with an UnknownBlock fallback) ------------


class _Block(BaseModel):
    model_config = ConfigDict(extra="allow")


class TextBlock(_Block):
    type: Literal["text"] = "text"
    text: str


class ThinkingBlock(_Block):
    type: Literal["thinking"] = "thinking"
    # thinking text is persisted empty by the CLI; the signature is what survives.
    thinking: str = ""
    signature: str | None = None


class ToolUseBlock(_Block):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict = Field(default_factory=dict)
    # NOTE(claude): `caller` (e.g. {"type": "direct"}) rides on the tool_use block in
    # newer CLIs to record what dispatched the call. Declared opaque `Any` like
    # snapshot/toolUseResult — preserved verbatim, never interpreted nor recursed for
    # extras (so its interior `type` key is not itself reported as forward drift).
    caller: Any | None = None


class ToolResultBlock(_Block):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str | None = None
    content: str | list | None = None
    is_error: bool | None = None


class UnknownBlock(_Block):
    """Fallback for a content block whose ``type`` this schema version does not know."""

    type: str


_KNOWN_BLOCK_TYPES = frozenset({"text", "thinking", "tool_use", "tool_result"})


def _block_discriminator(value: Any) -> str:
    """Route a block to its model by ``type``, defaulting unknowns to the fallback tag."""
    if isinstance(value, dict):
        tag = value.get("type")
    else:
        tag = getattr(value, "type", None)
    return tag if tag in _KNOWN_BLOCK_TYPES else "unknown"


Block = Annotated[
    Union[
        Annotated[TextBlock, Tag("text")],
        Annotated[ThinkingBlock, Tag("thinking")],
        Annotated[ToolUseBlock, Tag("tool_use")],
        Annotated[ToolResultBlock, Tag("tool_result")],
        Annotated[UnknownBlock, Tag("unknown")],
    ],
    Discriminator(_block_discriminator),
]


# --- Block normalization ----------------------------------------------------------------


def _tool_result_text(content: str | list | None) -> str | None:
    """Flatten a tool_result ``content`` (string or block list) into display text."""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts) if parts else None


def _block_to_normalized(block: BaseModel) -> NormalizedBlock:
    if isinstance(block, TextBlock):
        return NormalizedBlock("text", block.text, None, None, None, None)
    if isinstance(block, ThinkingBlock):
        payload = {"signature": block.signature} if block.signature is not None else None
        return NormalizedBlock("thinking", block.thinking, None, None, None, payload)
    if isinstance(block, ToolUseBlock):
        return NormalizedBlock("tool_use", None, block.name, block.id, None, dict(block.input))
    if isinstance(block, ToolResultBlock):
        text = _tool_result_text(block.content)
        # Preserve the structured content so the interpreter can recover nested sub-blocks.
        payload = {"content": block.content} if isinstance(block.content, list) else None
        return NormalizedBlock("tool_result", text, None, block.tool_use_id, block.is_error, payload)
    # UnknownBlock (or any future _Block subtype): keep the raw shape under its own kind.
    kind = getattr(block, "type", "unknown")
    return NormalizedBlock(kind, None, None, None, None, block.model_dump())


def _normalize_content(content: str | list | None) -> list[NormalizedBlock]:
    if content is None:
        return []
    if isinstance(content, str):
        return [NormalizedBlock("text", content, None, None, None, None)]
    return [_block_to_normalized(b) for b in content]


# --- Records ----------------------------------------------------------------------------


class BaseRecord(BaseModel):
    """Common base for every registered record type.

    Provides the tolerant config and the empty-by-default :meth:`blocks` contract so the
    interpreter can call ``record.blocks()`` on *any* record without a type check.
    """

    model_config = ConfigDict(extra="allow")

    type: str

    def blocks(self) -> list[NormalizedBlock]:
        return []


class Envelope(BaseRecord):
    """Shared header of the conversational record family.

    Every field may be null or absent in the wild (the archive must ingest imperfect data),
    hence all are optional.
    """

    uuid: str | None = None
    parentUuid: str | None = None
    sessionId: str | None = None
    timestamp: str | None = None
    cwd: str | None = None
    version: str | None = None
    gitBranch: str | None = None
    isSidechain: bool | None = None
    userType: str | None = None
    entrypoint: str | None = None
    # Envelope-level provenance seen across user/assistant/attachment records in the first
    # production import: the emitting agent's id and its human-readable session slug.
    agentId: str | None = None
    slug: str | None = None
    # Verified on user AND system records (residual family): flags a CLI-internal record.
    isMeta: bool | None = None
    # Snake_case sibling of sessionId, verified on all four envelope families. Kept as a
    # distinct field (never merged with sessionId) so the raw shape round-trips losslessly.
    session_id: str | None = None


class UserMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str | None = None
    content: str | list[Block]


class Usage(BaseModel):
    # Survey-known usage keys are declared so ordinary records parse "ok"; sub-structures
    # we never interpret (cache_creation, server_tool_use, iterations) are opaque `Any`.
    # Truly-unknown usage keys still land in model_extra and count as forward drift.
    model_config = ConfigDict(extra="allow")

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation: Any | None = None
    server_tool_use: Any | None = None
    service_tier: str | None = None
    speed: str | None = None
    iterations: Any | None = None
    inference_geo: str | None = None


class AssistantMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str | None = None
    # `type` here is the message-level kind the CLI echoes (always "message" in the wild),
    # distinct from the record-level `type` discriminator and the block-level `type` tag.
    type: str | None = None
    id: str | None = None
    model: str | None = None
    content: list[Block]
    usage: Usage | None = None
    stop_reason: str | None = None
    stop_sequence: str | None = None
    stop_details: Any | None = None  # opaque: preserved, never interpreted
    diagnostics: Any | None = None  # opaque: preserved, never interpreted


class UserRecord(Envelope):
    type: Literal["user"] = "user"
    message: UserMessage
    permissionMode: str | None = None
    promptId: str | None = None
    promptSource: str | None = None
    # Links a tool_result-bearing user record back to the assistant turn that issued the
    # tool_use (user-record-only in the first production import).
    sourceToolAssistantUUID: str | None = None
    # ... and back to the specific tool_use id (user-record-only, residual family).
    sourceToolUseID: str | None = None
    # NOTE(claude): `origin` (e.g. {"kind": "coordinator"}) is a structured payload —
    # opaque `Any` per the snapshot/toolUseResult precedent, never recursed for extras.
    origin: Any | None = None
    # NOTE(claude): opaque echo of a tool result payload (per spec §3 survey). Declared
    # `Any` so it is preserved verbatim but never recursed for extras nor interpreted.
    toolUseResult: Any | None = None

    def blocks(self) -> list[NormalizedBlock]:
        return _normalize_content(self.message.content)


class AssistantRecord(Envelope):
    type: Literal["assistant"] = "assistant"
    message: AssistantMessage
    requestId: str | None = None
    # Attribution of the turn to a dispatching agent / skill / plugin / MCP server+tool
    # (assistant-record-only in the production import).
    attributionAgent: str | None = None
    attributionSkill: str | None = None
    attributionPlugin: str | None = None
    attributionMcpServer: str | None = None
    attributionMcpTool: str | None = None
    # Assistant-level error marker is a plain string in the wild (e.g. "server_error") —
    # unlike the structured dict of the same name on system api_error records.
    error: str | None = None

    def blocks(self) -> list[NormalizedBlock]:
        return _normalize_content(self.message.content)


class SystemRecord(Envelope):
    type: Literal["system"] = "system"
    subtype: str | None = None
    level: str | None = None
    content: str | None = None
    # -- subtype "turn_duration" payload (verified in the production residual) --
    durationMs: int | None = None
    messageCount: int | None = None
    pendingBackgroundAgentCount: int | None = None
    # -- subtype "stop_hook_summary" payload --
    hookCount: int | None = None
    # NOTE(claude): hookInfos/hookErrors/hookAdditionalContext are lists of structured
    # hook payloads — opaque `Any` per the snapshot/toolUseResult precedent (preserved
    # verbatim, never recursed for extras).
    hookInfos: Any | None = None
    hookErrors: Any | None = None
    hookAdditionalContext: Any | None = None
    preventedContinuation: bool | None = None
    stopReason: str | None = None
    hasOutput: bool | None = None
    toolUseID: str | None = None
    # -- subtype "api_error" payload --
    # `error` here is the structured API error dict (status/headers/nested error...) and
    # `cause` the transport-level cause ({"code": "ECONNRESET", ...}): both opaque `Any`.
    error: Any | None = None
    cause: Any | None = None
    retryInMs: float | None = None
    retryAttempt: int | None = None
    maxRetries: int | None = None


class AttachmentRecord(Envelope):
    type: Literal["attachment"] = "attachment"
    # NOTE(claude): the attachment body (e.g. {"type": "deferred_tools_delta", ...}) is a
    # structured, kind-varying blob. Declared opaque `Any` like snapshot/toolUseResult:
    # preserved verbatim, never *modeled* (no sub-BaseModel) nor recursed for extras — which is
    # why all 805 production attachments parse "ok" regardless of their body shape. `blocks()`
    # peeks at ONE known shape (the human-origin queued_command) to rescue its text; it reads the
    # raw dict directly rather than declaring a sub-model, so this interpretation never widens the
    # anomaly surface. See task-p4-f1-brief.md.
    attachment: Any | None = None

    def blocks(self) -> list[NormalizedBlock]:
        """Rescue a human-typed queued prompt as one text block; every other body yields none.

        Almost every attachment is harness furniture (deferred_tools_delta, skill_listing,
        task_reminder, ...) with no conversational content. The ONE exception is a
        ``queued_command`` the human typed: its only home in the conversation DAG is this
        attachment record (the assistant's next turn parents to this record's uuid), so the
        verbatim prompt would otherwise be invisible everywhere and absent from search. The
        three-way guard is load-bearing — the furniture ``commandMode == "task-notification"``
        variant ALSO carries a ``prompt``, and a ``commandMode == "prompt"`` issued by a
        non-human origin is not a human turn — so both must stay silent. Field paths were
        verified against the two real production records read-only (attachment.type /
        commandMode / origin.kind / prompt).
        """
        att = self.attachment
        if not isinstance(att, dict):
            return []
        if att.get("type") != "queued_command" or att.get("commandMode") != "prompt":
            return []
        origin = att.get("origin")
        if not isinstance(origin, dict) or origin.get("kind") != "human":
            return []
        prompt = att.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            return []
        return [NormalizedBlock("text", prompt, None, None, None, None)]


class ThinMetaRecord(BaseRecord):
    """Base for the envelope-less metadata records (a session id is the only shared field)."""

    sessionId: str | None = None


class AiTitleRecord(ThinMetaRecord):
    type: Literal["ai-title"] = "ai-title"
    aiTitle: str | None = None


class CustomTitleRecord(ThinMetaRecord):
    type: Literal["custom-title"] = "custom-title"
    customTitle: str | None = None


class ModeRecord(ThinMetaRecord):
    type: Literal["mode"] = "mode"
    mode: str | None = None


class PermissionModeRecord(ThinMetaRecord):
    type: Literal["permission-mode"] = "permission-mode"
    permissionMode: str | None = None


class LastPromptRecord(ThinMetaRecord):
    type: Literal["last-prompt"] = "last-prompt"
    leafUuid: str | None = None
    # The verbatim last-prompt text the CLI records alongside the leaf uuid (may be absent).
    lastPrompt: str | None = None


class QueueOperationRecord(ThinMetaRecord):
    type: Literal["queue-operation"] = "queue-operation"
    operation: str | None = None
    content: str | None = None
    timestamp: str | None = None


class AgentNameRecord(ThinMetaRecord):
    type: Literal["agent-name"] = "agent-name"
    agentName: str | None = None


class AgentColorRecord(ThinMetaRecord):
    # Retired meta type from an older CLI (surfaced as `unknown_record_type` in the first
    # production import). Declared as a thin-meta so archived sessions parse "ok"; carries
    # only the assigned agent color alongside the shared sessionId.
    type: Literal["agent-color"] = "agent-color"
    agentColor: str | None = None


class FileHistorySnapshotRecord(ThinMetaRecord):
    # NOTE(claude): `snapshot` is declared as an *opaque* Any field — anomalies signal
    # drift-we-don't-understand, and the snapshot payload is drift-we-decided-never-to-
    # interpret (archive-only per spec). It is preserved verbatim on the record but its
    # interior is never modeled and never recursed for extras.
    type: Literal["file-history-snapshot"] = "file-history-snapshot"
    messageId: str | None = None
    snapshot: Any | None = None
    isSnapshotUpdate: bool | None = None


# --- Extras collection (recursive) ------------------------------------------------------


def collect_extra_fields(model: BaseModel) -> list[str]:
    """Return every unknown extra field name found anywhere in a validated record tree.

    Walks the model plus its nested sub-models (message, content blocks, usage, ...) so a
    forward-drift field is reported no matter how deep it appears. Extra *values* are raw
    (unvalidated) and are not descended into — only declared sub-model fields are.
    """
    names: list[str] = []
    seen: set[int] = set()
    stack: list[BaseModel] = [model]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if current.model_extra:
            names.extend(current.model_extra.keys())
        for field_name in type(current).model_fields:
            value = getattr(current, field_name, None)
            if isinstance(value, BaseModel):
                stack.append(value)
            elif isinstance(value, list):
                stack.extend(item for item in value if isinstance(item, BaseModel))
    return names
