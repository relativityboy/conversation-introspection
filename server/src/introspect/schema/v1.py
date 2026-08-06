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
SCHEMA_VERSION = "introspect-schema/5"

# Per-generation, human-readable old-vs-new summaries. This is the runtime source of truth
# the ``schema_versions`` provenance table self-populates from (see
# ``introspect.schema_versions.ensure_current_schema_version_recorded``): when this codebase
# first runs import/reparse against a DB, the row for the *current* SCHEMA_VERSION is stamped
# with ``DIFF_NOTES[SCHEMA_VERSION]``. Migration 0005 backfills the historical rows (/1../3)
# with frozen copies of these same notes. Notes are written from the real diffs/reports
# (task-schema2-report.md, task-p4-f1-report.md, task-p4-f7-report.md).
DIFF_NOTES: dict[str, str] = {
    "introspect-schema/1": (
        "Initial tolerant transcript-record registry: the conversational envelope family "
        "(user/assistant/system/attachment), the thin-meta records (ai-title, custom-title, "
        "mode, permission-mode, last-prompt, queue-operation, agent-name, "
        "file-history-snapshot), and the never-raise parse_line contract (forward drift is an "
        "info anomaly, unknown record type a warn, bad JSON/validation an error)."
    ),
    "introspect-schema/2": (
        "First production-drift pass. Declared forward-drift fields at their verified "
        "locations: agentId/slug and isMeta/session_id on the Envelope; attribution* "
        "(Agent/Skill/Plugin/McpServer/McpTool) and a plain-string error on AssistantRecord; "
        "sourceToolAssistantUUID/sourceToolUseID and opaque origin on UserRecord; the "
        "message-level type echo; the opaque tool_use caller; the SystemRecord subtype "
        "payloads (turn_duration, stop_hook_summary, api_error) with opaque error/cause/hook* "
        "blobs; the opaque attachment body; and lastPrompt. Added AgentColorRecord for the "
        "retired agent-color meta type. (task-schema2-report.md)"
    ),
    "introspect-schema/3": (
        "Interpretation change only, no new declared fields. AttachmentRecord.blocks() now "
        "rescues a human-origin queued_command attachment (commandMode == 'prompt' and "
        "origin.kind == 'human') into one searchable text block; every other attachment shape "
        "stays zero-block. The attachment body remains opaque Any, so the anomaly floor is "
        "unchanged. (task-p4-f1-report.md)"
    ),
    "introspect-schema/4": (
        "Second production-drift pass (new CLI versions ~2.1.207-2.1.215). Declared: effort "
        "(reasoning effort), isApiErrorMessage, apiErrorStatus and supersedesUuids on "
        "AssistantRecord; opaque container/context_management on the assistant message; "
        "toolEndsTurn, interruptedMessageId, toolDenialKind, classifierMetaLines on "
        "UserRecord; the model_refusal_fallback SystemRecord field family "
        "(trigger/direction/originalModel/fallbackModel/requestId/apiRefusalCategory/"
        "apiRefusalExplanation/refusedUserMessageUuid + opaque retractedMessageUuids). Added "
        "the ModelFallbackBlock content block (type 'fallback', opaque from/to) and the "
        "FileHistoryDeltaRecord type (file-history-delta, opaque backup). Drove the info "
        "anomaly floor from ~3,596 to ~0. (task-p4-f7-report.md)"
    ),
    "introspect-schema/5": (
        "Third production-drift pass (CLI ~2.1.219-2.1.220). Declared: interruptedByShutdown, "
        "source, userFeedback (opaque), logicalParentUuid, compactMetadata (opaque), "
        "isVisibleInTranscriptOnly and isCompactSummary on UserRecord; isAbortedMidStream and "
        "pendingWorkflowCount on AssistantRecord. Census-driven; expected to drive the "
        "unknown_field floor from 24 to ~0. (2026-08-05 anomaly census)"
    ),
}


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


class ModelFallbackBlock(_Block):
    """A model-fallback marker the CLI records inline in an assistant turn.

    Shape on disk: ``{"type": "fallback", "from": {"model": ...}, "to": {"model": ...}}`` —
    it marks where a request fell back from one model to another. ``from`` is a Python keyword,
    so both keys are declared via aliases; the payloads are opaque ``Any`` (structured, but we
    never interpret them — same treatment as ``caller``/``snapshot``), so they are preserved
    verbatim and never recursed for extras.
    """

    type: Literal["fallback"] = "fallback"
    from_: Any | None = Field(default=None, alias="from")
    to: Any | None = None


class UnknownBlock(_Block):
    """Fallback for a content block whose ``type`` this schema version does not know."""

    type: str


_KNOWN_BLOCK_TYPES = frozenset({"text", "thinking", "tool_use", "tool_result", "fallback"})


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
        Annotated[ModelFallbackBlock, Tag("fallback")],
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
    if isinstance(block, ModelFallbackBlock):
        # A non-conversational marker: no text. Dump by_alias so the payload keeps the on-disk
        # key names (``from``/``to``) rather than the aliased python field name (``from_``).
        return NormalizedBlock("fallback", None, None, None, None, block.model_dump(by_alias=True))
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
    # NOTE(claude): the API "container" and "context_management" features ride on the message
    # object and are null in the current production drift. Declared opaque `Any` (not a guessed
    # concrete type) so a future populated shape stays benign info-drift, never a validation
    # error — parse_line grades ValidationError as an ERROR anomaly, which is worse than the
    # info drift we are clearing.
    container: Any | None = None
    context_management: Any | None = None


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
    # Second production-drift family (schema/4), user-record-only: a tool_result-bearing user
    # turn may end the turn (toolEndsTurn), record the interrupted assistant message
    # (interruptedMessageId), why a tool was denied (toolDenialKind: "user-rejected" /
    # "automode-blocked"), or carry a classifier's meta lines (classifierMetaLines, a string).
    toolEndsTurn: bool | None = None
    interruptedMessageId: str | None = None
    toolDenialKind: str | None = None
    classifierMetaLines: str | None = None
    # Third production-drift family (schema/5, CLI ~2.1.219-2.1.220), user-record-only:
    # interruptedByShutdown flags an aborted turn; source notes its origin; userFeedback and
    # compactMetadata are opaque payloads; logicalParentUuid is a parent reference; the
    # visibility/summary flags mark presentation.
    interruptedByShutdown: bool | None = None
    source: str | None = None
    userFeedback: Any | None = None
    logicalParentUuid: str | None = None
    compactMetadata: Any | None = None
    isVisibleInTranscriptOnly: bool | None = None
    isCompactSummary: bool | None = None

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
    # Second production-drift family (schema/4): `effort` is the reasoning-effort token
    # ("xhigh", ...) and dominated the drift floor; isApiErrorMessage/apiErrorStatus flag an
    # assistant turn synthesized from an API error. supersedesUuids is a list of message uuids
    # this turn supersedes — declared opaque `Any` (list payload precedent: hookInfos), so its
    # elements are preserved verbatim, never validated element-wise into a possible error.
    effort: str | None = None
    isApiErrorMessage: bool | None = None
    apiErrorStatus: int | None = None
    supersedesUuids: Any | None = None
    # Third production-drift family (schema/5, CLI ~2.1.219-2.1.220), assistant-record-only:
    # isAbortedMidStream flags incomplete turns; pendingWorkflowCount records async workflows.
    isAbortedMidStream: bool | None = None
    pendingWorkflowCount: int | None = None

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
    # -- subtype "model_refusal_fallback" payload (schema/4 drift) --
    # Records a fallback triggered by an API model refusal: the retry direction and the
    # original/fallback model, the refusal category + explanation, and the uuids of the
    # refused/retracted messages. `requestId` is declared locally here (it already lives on
    # AssistantRecord with the same meaning; the two record classes are independent, so this is
    # not a shared-Envelope refactor). `retractedMessageUuids` is opaque `Any` (list payload
    # precedent) so its elements are never validated element-wise into a possible error.
    trigger: str | None = None
    direction: str | None = None
    originalModel: str | None = None
    fallbackModel: str | None = None
    requestId: str | None = None
    apiRefusalCategory: str | None = None
    apiRefusalExplanation: str | None = None
    retractedMessageUuids: Any | None = None
    refusedUserMessageUuid: str | None = None


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


class FileHistoryDeltaRecord(ThinMetaRecord):
    # NOTE(claude): sibling of FileHistorySnapshotRecord (surfaced as `unknown_record_type` in
    # the second production drift). Records one file backup delta between snapshots. Like the
    # snapshot record, `backup` is an *opaque* Any field — file-history payloads are
    # drift-we-decided-never-to-interpret (archive-only per spec), preserved verbatim and never
    # recursed for extras.
    type: Literal["file-history-delta"] = "file-history-delta"
    messageId: str | None = None
    snapshotMessageId: str | None = None
    trackingPath: str | None = None
    backup: Any | None = None
    timestamp: str | None = None


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
