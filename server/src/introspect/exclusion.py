"""Project-exclusion core (spec 2026-08-17 §2), shared by the TUI /exclude and the CLI verb.

One logic, two phrasings: both surfaces call these functions so the wall can never behave
differently depending on how it was raised. The management verbs -- ``add_exclusion``,
``remove_exclusion``, ``list_exclusions`` -- remain owner-only and unreachable from the API.
``excluded_project_slugs`` is the one exception: a read-only query the API MAY use, so that
read paths which touch disk directly (the memories route, the first of its kind) can keep
excluded projects invisible without reaching into TUI/CLI-only territory. All functions take
an injected ORM session (hermetic tests, same rule as CrontabIO/skills)."""

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


def excluded_project_slugs(db: Session) -> frozenset[str]:
    """Every excluded project's ``dir_slug``, for read paths that must skip them.

    The single read-only query the API may call directly (see module docstring). Reveals
    nothing beyond what the owner already excluded -- no reason, no timestamp, no verbs.
    """
    return frozenset(row.dir_slug for row in db.query(ExcludedProject).all())
