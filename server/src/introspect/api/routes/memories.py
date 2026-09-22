"""Memories endpoint (memories board Task 1): ``GET /api/v1/memories``.

Lists Claude Code auto-memory files straight off disk (:mod:`introspect.memories`) -- the
only API read path that lists directories and reads file content off disk rather than the
captured archive. It gates itself on project exclusion via
:func:`introspect.exclusion.excluded_project_slugs`: an excluded project is simply absent from
the response, with no hint that anything was hidden -- an excluded project is indistinguishable
from nonexistent, matching the zero-read guarantee of
``docs/superpowers/specs/2026-08-17-exclusion-and-deletion-design.md`` §2. Nothing here is
captured, indexed, or written.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from introspect.api.deps import get_db
from introspect.api.models import MemoryOut, MemoryProjectOut
from introspect.exclusion import excluded_project_slugs
from introspect.memories import scan_memories
from introspect.models import Project

router = APIRouter(prefix="/api/v1")


class MemoryList(BaseModel):
    projects: list[MemoryProjectOut]


@router.get("/memories", response_model=MemoryList)
def list_memories(request: Request, db: Session = Depends(get_db)) -> MemoryList:
    root = request.app.state.source_root
    excluded = excluded_project_slugs(db)
    scanned = scan_memories(root, excluded=excluded)

    slugs = [project.dir_slug for project in scanned]
    resolved_cwds: dict[str, str | None] = {}
    if slugs:
        resolved_cwds = dict(
            db.execute(
                select(Project.dir_slug, Project.resolved_cwd).where(Project.dir_slug.in_(slugs))
            ).all()
        )

    projects = [
        MemoryProjectOut(
            dir_slug=project.dir_slug,
            resolved_cwd=resolved_cwds.get(project.dir_slug),
            memories=[MemoryOut.model_validate(memory) for memory in project.memories],
        )
        for project in scanned
    ]
    return MemoryList(projects=projects)
