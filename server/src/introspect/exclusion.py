"""Project-exclusion core (spec 2026-08-17 §2), shared by the TUI /exclude and the CLI verb.

One logic, two phrasings: both surfaces call these functions so the wall can never behave
differently depending on how it was raised. Owner-only by construction — nothing here is
reachable from the API. All functions take an injected ORM session (hermetic tests, same
rule as CrontabIO/skills)."""

from __future__ import annotations

import uuid as uuid_mod
from dataclasses import dataclass

from sqlalchemy.orm import Session

from introspect.models import ChatSession, ExcludedProject, ExcludedSession, Project
from introspect.slugs import slug_for_path


def is_session_uuid(target: str) -> bool:
    """A uuid target addresses the SESSION wall; anything else the project wall."""
    try:
        uuid_mod.UUID(target)
    except ValueError:
        return False
    return True


def resolve_slug(target: str) -> str:
    """A filesystem path (absolute, ``~``, or relative) encodes via the CLI's slug rule;
    anything else is treated as an already-encoded slug."""
    if target.startswith(("/", "~", ".")):
        return slug_for_path(target)
    return target


@dataclass(frozen=True)
class AddOutcome:
    kind: str  # 'project' | 'session'
    slug: str  # the slug, or the session uuid
    already_excluded: bool
    #: Sessions already captured for this project (prevention-only honesty: they REMAIN
    #: until the deletion tool repairs them). 0 for never-captured projects and sessions.
    prior_sessions: int


def add_exclusion(db: Session, target: str, reason: str | None) -> AddOutcome:
    from introspect.ingest.capture import utcnow

    if is_session_uuid(target):
        if db.get(ExcludedSession, target) is not None:
            return AddOutcome("session", target, already_excluded=True, prior_sessions=0)
        db.add(ExcludedSession(session_uuid=target, reason=reason, created_at=utcnow()))
        db.commit()
        return AddOutcome("session", target, already_excluded=False, prior_sessions=0)

    slug = resolve_slug(target)
    if db.get(ExcludedProject, slug) is not None:
        return AddOutcome("project", slug, already_excluded=True, prior_sessions=0)
    db.add(ExcludedProject(dir_slug=slug, reason=reason, created_at=utcnow()))
    db.commit()
    prior = 0
    project = db.query(Project).filter(Project.dir_slug == slug).one_or_none()
    if project is not None:
        prior = db.query(ChatSession).filter(ChatSession.project_id == project.id).count()
    return AddOutcome("project", slug, already_excluded=False, prior_sessions=prior)


def remove_exclusion(db: Session, target: str) -> tuple[str, bool]:
    """Returns ``(slug_or_uuid, removed)`` — ``removed`` False when it wasn't excluded."""
    if is_session_uuid(target):
        row = db.get(ExcludedSession, target)
        if row is None:
            return target, False
        db.delete(row)
        db.commit()
        return target, True
    slug = resolve_slug(target)
    row = db.get(ExcludedProject, slug)
    if row is None:
        return slug, False
    db.delete(row)
    db.commit()
    return slug, True


def list_exclusions(
    db: Session,
) -> tuple[list[ExcludedProject], list[ExcludedSession]]:
    """Both walls: (projects, sessions), each sorted."""
    return (
        db.query(ExcludedProject).order_by(ExcludedProject.dir_slug).all(),
        db.query(ExcludedSession).order_by(ExcludedSession.session_uuid).all(),
    )
