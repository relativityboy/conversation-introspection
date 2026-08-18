"""Source discovery: walk a Claude Code transcript root and classify every file on disk.

Recognizes exactly three on-disk shapes (see task-4-brief.md) directly under one level of
project directories:

* **main**     -- ``<root>/<slug>/<uuid>.jsonl``, stem parses as a UUID.
* **backup**   -- ``<root>/<slug>/<uuid>.jsonl.bak-<epoch>``, an older snapshot of a main
  file kept alongside it by the CLI.
* **subagent** -- ``<root>/<slug>/<session-uuid>/subagents/agent-<hex>.jsonl``, a sidechain
  transcript spawned by a tool call in the parent session, with an optional sibling
  ``agent-<hex>.meta.json`` describing it.

Everything else (stray files, non-UUID names, unrelated directories) is skipped silently —
this walks real user directories and must never raise on the unexpected.
"""

from __future__ import annotations

import json
import re
import uuid as uuid_mod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_BACKUP_RE = re.compile(r"^(?P<uuid>.+)\.jsonl\.bak-\d+$")
_SUBAGENT_FILE_RE = re.compile(r"^agent-(?P<hex>[0-9a-fA-F]+)\.jsonl$")


def _is_uuid(candidate: str) -> bool:
    try:
        uuid_mod.UUID(candidate)
    except ValueError:
        return False
    return True


@dataclass
class AgentMeta:
    agent_type: str | None
    description: str | None
    tool_use_id: str | None


@dataclass
class DiscoveredFile:
    path: Path
    project_slug: str  # source dir name, e.g. "-Users-relativityboy-projects--ai-jetwalls"
    session_uuid: str
    kind: str  # 'main' | 'subagent' | 'backup'
    agent_hex_id: str | None  # subagents only, from filename agent-<hex>.jsonl
    agent_meta: AgentMeta | None


def _load_agent_meta(meta_path: Path) -> AgentMeta | None:
    """Read a subagent's sibling ``.meta.json``. Missing or corrupt -> None, never raises."""
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return AgentMeta(
        agent_type=data.get("agentType"),
        description=data.get("description"),
        tool_use_id=data.get("toolUseId"),
    )


def _discover_subagents(session_dir: Path, slug: str) -> Iterator[DiscoveredFile]:
    session_uuid = session_dir.name
    if not _is_uuid(session_uuid):
        return
    subagents_dir = session_dir / "subagents"
    if not subagents_dir.is_dir():
        return
    for entry in subagents_dir.iterdir():
        if not entry.is_file():
            continue
        match = _SUBAGENT_FILE_RE.match(entry.name)
        if not match:
            continue
        hex_id = match.group("hex")
        meta_path = subagents_dir / f"agent-{hex_id}.meta.json"
        yield DiscoveredFile(
            path=entry,
            project_slug=slug,
            session_uuid=session_uuid,
            kind="subagent",
            agent_hex_id=hex_id,
            agent_meta=_load_agent_meta(meta_path),
        )


def _discover_project(
    project_dir: Path, excluded_sessions: frozenset[str] | set[str] = frozenset()
) -> Iterator[DiscoveredFile]:
    slug = project_dir.name
    for entry in project_dir.iterdir():
        if entry.is_dir():
            # An excluded session's uuid IS the subagent container dir's name — skipping
            # here means its subagent files and meta.json are never read (spec 2026-08-17
            # §3 zero-read rule, session level).
            if entry.name in excluded_sessions:
                continue
            yield from _discover_subagents(entry, slug)
            continue
        if not entry.is_file():
            continue
        name = entry.name
        if name.endswith(".jsonl"):
            stem = name[: -len(".jsonl")]
            if _is_uuid(stem) and stem not in excluded_sessions:
                yield DiscoveredFile(
                    path=entry,
                    project_slug=slug,
                    session_uuid=stem,
                    kind="main",
                    agent_hex_id=None,
                    agent_meta=None,
                )
            continue
        match = _BACKUP_RE.match(name)
        if (
            match
            and _is_uuid(match.group("uuid"))
            and match.group("uuid") not in excluded_sessions
        ):
            yield DiscoveredFile(
                path=entry,
                project_slug=slug,
                session_uuid=match.group("uuid"),
                kind="backup",
                agent_hex_id=None,
                agent_meta=None,
            )


def discover(
    root: Path,
    excluded: frozenset[str] | set[str] = frozenset(),
    excluded_sessions: frozenset[str] | set[str] = frozenset(),
) -> Iterator[DiscoveredFile]:
    """Walk one level of project directories under ``root`` and yield known file kinds.

    Output is sorted by path for deterministic ordering. Never raises: entries that don't
    match a known shape are skipped silently.

    ``excluded`` slugs are skipped at the DIRECTORY level, before reading anything beneath
    them — not filenames, not agent-meta.json (spec 2026-08-17 §2 zero-read rule: an
    excluded project's content must never be read, even incidentally). ``excluded_sessions``
    uuids are skipped by FILENAME (main/backup files) and by container-dir name (subagents)
    — the session-level wall of the same rule (§3 resurrection guard).
    """
    found: list[DiscoveredFile] = []
    for project_dir in root.iterdir():
        if not project_dir.is_dir():
            continue
        if project_dir.name in excluded:
            continue
        found.extend(_discover_project(project_dir, excluded_sessions))
    found.sort(key=lambda f: f.path)
    yield from found
