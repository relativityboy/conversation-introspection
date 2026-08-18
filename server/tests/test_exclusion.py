"""Project exclusion (spec 2026-08-17 §2): prevention before capture, zero-read skips.

Fixture-driven throughout; the excluded_projects table is user-data-layer (import/reparse
never touch it, same invariant family as favorites)."""

from __future__ import annotations

from pathlib import Path


from introspect.db import get_engine, session_factory, upgrade_to_head
from introspect.ingest.discovery import discover
from introspect.ingest.reparse import reparse_all
from introspect.ingest.run import run_import
from introspect.models import ChatSession, ExcludedProject, Project, SourceFile
from tests.conftest import PROJECT_SLUG_1, PROJECT_SLUG_2, SESSION_UUID_3


def _factory_for(db_path: Path):
    engine = get_engine(db_path)
    upgrade_to_head(engine)
    return session_factory(engine)


def _exclude(db_path: Path, slug: str, reason: str | None = None) -> None:
    factory = _factory_for(db_path)
    with factory() as db:
        from introspect.ingest.capture import utcnow

        db.add(ExcludedProject(dir_slug=slug, reason=reason, created_at=utcnow()))
        db.commit()


def test_discover_skips_excluded_project_dir(fixture_tree: Path) -> None:
    all_slugs = {f.project_slug for f in discover(fixture_tree)}
    assert {PROJECT_SLUG_1, PROJECT_SLUG_2} <= all_slugs

    remaining = list(discover(fixture_tree, excluded=frozenset({PROJECT_SLUG_1})))
    assert {f.project_slug for f in remaining} == all_slugs - {PROJECT_SLUG_1}
    # backup + subagent files under the excluded slug are gone too, not just mains
    # (component equality, not substring -- PROJECT_SLUG_1 is a prefix of PROJECT_SLUG_2)
    assert not any(
        parent.name == PROJECT_SLUG_1 for f in remaining for parent in f.path.parents
    )


def test_run_import_never_captures_excluded_project(
    tmp_path: Path, fixture_tree: Path
) -> None:
    dbp = tmp_path / "a.db"
    _exclude(dbp, PROJECT_SLUG_1, reason="sensitive client work")
    summary = run_import(dbp, fixture_tree)
    assert summary.status == "ok"

    factory = _factory_for(dbp)
    with factory() as db:
        slugs = {p.dir_slug for p in db.query(Project).all()}
        assert PROJECT_SLUG_1 not in slugs  # not even a project row exists
        sessions = {s.session_uuid for s in db.query(ChatSession).all()}
        assert sessions == {SESSION_UUID_3}  # only the non-excluded project landed


def test_excluding_after_capture_keeps_existing_and_stops_growth(
    tmp_path: Path, fixture_tree: Path
) -> None:
    dbp = tmp_path / "a.db"
    first = run_import(dbp, fixture_tree)
    assert first.records_added > 0

    _exclude(dbp, PROJECT_SLUG_1)
    second = run_import(dbp, fixture_tree)
    assert second.records_added == 0  # nothing new anywhere (fixture unchanged)

    factory = _factory_for(dbp)
    with factory() as db:
        # Existing capture is untouched (exclusion is prevention, not deletion)...
        assert db.query(ChatSession).count() == 3
        # ...and the excluded project's source rows must NOT flip gone_at_source: the
        # files are still on disk, we just stopped looking (spec §2 truthfulness rule).
        statuses = {
            sf.status
            for sf in db.query(SourceFile).all()
        }
        assert statuses == {"active"}


def test_excluded_projects_survive_reparse(tmp_path: Path, fixture_tree: Path) -> None:
    dbp = tmp_path / "a.db"
    run_import(dbp, fixture_tree)
    _exclude(dbp, PROJECT_SLUG_1, reason="keep out")
    factory = _factory_for(dbp)
    with factory() as db:
        reparse_all(db)
        row = db.query(ExcludedProject).one()
        assert row.dir_slug == PROJECT_SLUG_1
        assert row.reason == "keep out"


def test_run_import_with_no_exclusions_is_unchanged(
    tmp_path: Path, fixture_tree: Path
) -> None:
    summary = run_import(tmp_path / "a.db", fixture_tree)
    assert summary.status == "ok" and summary.records_added > 0


# --- Session-level exclusion (spec 2026-08-17 §3 resurrection guard) -----------------------


def _exclude_session(db_path: Path, session_uuid: str, reason: str | None = None) -> None:
    from introspect.ingest.capture import utcnow
    from introspect.models import ExcludedSession

    factory = _factory_for(db_path)
    with factory() as db:
        db.add(ExcludedSession(session_uuid=session_uuid, reason=reason, created_at=utcnow()))
        db.commit()


def test_discover_skips_excluded_session_files_by_name(fixture_tree: Path) -> None:
    from tests.conftest import SESSION_UUID_1

    all_files = list(discover(fixture_tree))
    assert any(f.session_uuid == SESSION_UUID_1 for f in all_files)
    # session 1 has main + backup + subagent files; ALL must vanish on the session wall,
    # while its sibling session in the SAME project directory survives.
    remaining = list(
        discover(fixture_tree, excluded_sessions=frozenset({SESSION_UUID_1}))
    )
    assert not any(f.session_uuid == SESSION_UUID_1 for f in remaining)
    assert {f.project_slug for f in remaining} == {f.project_slug for f in all_files}


def test_run_import_never_captures_excluded_session(
    tmp_path: Path, fixture_tree: Path
) -> None:
    from tests.conftest import SESSION_UUID_1, SESSION_UUID_2

    dbp = tmp_path / "a.db"
    _exclude_session(dbp, SESSION_UUID_1, reason="deleted; re-import forbidden")
    run_import(dbp, fixture_tree)
    factory = _factory_for(dbp)
    with factory() as db:
        sessions = {s.session_uuid for s in db.query(ChatSession).all()}
        assert SESSION_UUID_1 not in sessions
        assert {SESSION_UUID_2, SESSION_UUID_3} <= sessions  # same-project sibling intact
