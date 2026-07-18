"""Capture integrity tests (Task 7): restored-copy dedup, divergence with generations,
and gone-at-source detection.

The Claude Code CLI rewrites and deletes transcripts out from under us. Three failure
shapes must never lose or corrupt the archive:

* **restored/copied source at a DIFFERENT path** -> its lines dedup against the original
  (the discriminator is path identity: a new path means "another copy", not a change).
* **divergence** (SAME path, changed prefix) -> the old generation is frozen as
  ``diverged`` and a new generation is created and FULLY re-ingested (dedup bypassed) so
  the new primary is complete and byte-for-byte exportable.
* **gone at source** -> a previously-seen path that is neither rediscovered nor on disk is
  flipped ``gone_at_source`` without ever touching the raw bytes it captured.

The first three tests are the binding contract (verbatim from task-7-brief); the remainder
pin the edges the brief calls out (a divergence that also grew, gone-detection idempotency,
and the "still on disk" guard).
"""

import hashlib

from introspect.ingest.capture import capture_file, detect_gone
from introspect.ingest.discovery import discover
from introspect.models import ParseAnomaly, RawRecord, SourceFile
from tests.fixtures.records import make_user_line
from tests.test_capture import _capture_all

# --- Binding contract (verbatim from task-7-brief) --------------------------------------


def test_restored_copy_dedups(db_session, fixture_tree, tmp_path):
    _capture_all(db_session, fixture_tree)
    n = db_session.query(RawRecord).count()
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    restored_root = tmp_path / "restored"
    dst = restored_root / main.path.parent.name / main.path.name
    dst.parent.mkdir(parents=True)
    dst.write_bytes(main.path.read_bytes())
    for f in discover(restored_root):
        s = capture_file(db_session, f)
    assert db_session.query(RawRecord).count() == n
    assert s.records_skipped_duplicate > 0


def test_divergence_detected_and_regenerated(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    content = main.path.read_bytes()
    main.path.write_bytes(
        b'{"type":"user","message":{"role":"user","content":"REWRITTEN"},"uuid":"u-new1"}\n'
        + content[content.index(b"\n") + 1:]
    )
    _capture_all(db_session, fixture_tree)
    gens = db_session.query(SourceFile).filter_by(path=str(main.path)).all()
    assert {g.status for g in gens} == {"diverged", "active"}
    assert next(g for g in gens if g.status == "active").is_primary


def test_gone_at_source(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    victim = next(f for f in discover(fixture_tree) if f.kind == "main")
    victim.path.unlink()
    remaining = list(discover(fixture_tree))
    flipped = detect_gone(db_session, remaining)
    assert flipped == 1
    row = db_session.query(SourceFile).filter_by(path=str(victim.path)).one()
    assert row.status == "gone_at_source" and row.gone_detected_at is not None
    assert db_session.query(RawRecord).filter_by(source_file_id=row.id).count() > 0


# --- Edge cases the brief calls out ------------------------------------------------------


def test_divergence_regenerated_export_matches_grown_file(db_session, fixture_tree):
    """A rewrite that ALSO appends lines: the new generation must re-ingest the WHOLE file.

    Divergence is a full, dedup-bypassing re-ingest, so the new (active) generation's stored
    bytes must equal the rewritten file exactly, and its checkpoint/prefix_hash cover it all.
    """
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    original = main.path.read_bytes()
    rewritten = (
        b'{"type":"user","message":{"role":"user","content":"REWRITTEN"},"uuid":"u-grow1"}\n'
        + original[original.index(b"\n") + 1:]
        + make_user_line(text="appended after the rewrite")
    )
    main.path.write_bytes(rewritten)
    _capture_all(db_session, fixture_tree)

    active = db_session.query(SourceFile).filter_by(path=str(main.path), status="active").one()
    old = db_session.query(SourceFile).filter_by(path=str(main.path), status="diverged").one()
    assert active.generation == old.generation + 1
    assert active.is_primary is True
    assert old.is_primary is False

    rows = (
        db_session.query(RawRecord)
        .filter_by(source_file_id=active.id)
        .order_by(RawRecord.line_number)
        .all()
    )
    # Bypassed dedup => the new generation is a complete, byte-exact copy of the file.
    assert b"".join(r.raw_line for r in rows) == rewritten
    assert active.byte_offset_checkpoint == len(rewritten)
    assert active.prefix_hash == hashlib.sha256(rewritten).hexdigest()

    anomaly = db_session.query(ParseAnomaly).filter_by(kind="source_diverged").one()
    assert anomaly.severity == "error"
    assert anomaly.raw_record_id is None  # file-level anomaly
    assert anomaly.source_file_id == old.id


def test_detect_gone_is_idempotent(db_session, fixture_tree):
    """A second sweep over the same discovery flips nothing and preserves the first timestamp."""
    _capture_all(db_session, fixture_tree)
    victim = next(f for f in discover(fixture_tree) if f.kind == "main")
    victim.path.unlink()
    remaining = list(discover(fixture_tree))

    assert detect_gone(db_session, remaining) == 1
    row = db_session.query(SourceFile).filter_by(path=str(victim.path)).one()
    first_ts = row.gone_detected_at

    assert detect_gone(db_session, remaining) == 0
    db_session.refresh(row)
    assert row.status == "gone_at_source"
    assert row.gone_detected_at == first_ts


def test_detect_gone_keeps_file_present_on_disk(db_session, fixture_tree):
    """A path absent from the discovery list but still on disk is NOT flipped gone.

    Gone-at-source requires BOTH conditions; an empty discovery list must not orphan files
    that a partial/failed scan simply didn't enumerate.
    """
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    assert detect_gone(db_session, []) == 0
    row = db_session.query(SourceFile).filter_by(path=str(main.path)).one()
    assert row.status == "active"


# --- Reappearance after gone_at_source ---------------------------------------------------


def _make_gone(db_session, fixture_tree):
    """Capture everything, delete the main file, sweep it gone. Returns (main, its bytes)."""
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    content = main.path.read_bytes()
    main.path.unlink()
    assert detect_gone(db_session, list(discover(fixture_tree))) == 1
    return main, content


def test_gone_file_restored_identical_resumes_same_row(db_session, fixture_tree):
    """Byte-identical restore of a gone file: SAME row reactivates, tail ingest resumes.

    No new generation, gone_detected_at cleared, and a file-level info 'source_reappeared'
    anomaly records the event. The common restore-from-backup case.
    """
    main, content = _make_gone(db_session, fixture_tree)
    main.path.write_bytes(content)
    with main.path.open("ab") as fh:
        fh.write(make_user_line(text="appended after the restore"))

    stats = _capture_all(db_session, fixture_tree)

    row = db_session.query(SourceFile).filter_by(path=str(main.path)).one()  # one() = no new gen
    assert row.status == "active"
    assert row.gone_detected_at is None
    assert stats[main.path].records_added == 1  # only the post-restore tail was ingested

    anomaly = db_session.query(ParseAnomaly).filter_by(kind="source_reappeared").one()
    assert anomaly.severity == "info"
    assert anomaly.raw_record_id is None  # file-level anomaly
    assert anomaly.source_file_id == row.id


def test_gone_file_replaced_longer_diverges_with_reappeared_flag(db_session, fixture_tree):
    """A gone path coming back with DIFFERENT (and longer) bytes is a divergence, not a resume.

    The hazard is tail-grafting: size >= old checkpoint would let a naive resume append the
    new file's tail onto the dead row's records. Instead: old generation stays frozen with
    its originally captured bytes intact, and a new generation full-re-ingests the file.
    """
    main, original = _make_gone(db_session, fixture_tree)
    replacement = (
        b'{"type":"user","message":{"role":"user","content":"REPLACED"},"uuid":"u-re1"}\n'
        + original[original.index(b"\n") + 1:]
        + make_user_line(text="tail reaching beyond the old checkpoint")
    )
    assert len(replacement) > len(original)  # size check alone must NOT clear it
    main.path.write_bytes(replacement)

    _capture_all(db_session, fixture_tree)

    gens = (
        db_session.query(SourceFile)
        .filter_by(path=str(main.path))
        .order_by(SourceFile.generation)
        .all()
    )
    assert len(gens) == 2
    old, new = gens
    assert old.status == "diverged" and old.is_primary is False
    assert new.status == "active" and new.is_primary is True

    # No tail-grafting: the old generation still reconstructs EXACTLY what it captured.
    old_rows = (
        db_session.query(RawRecord)
        .filter_by(source_file_id=old.id)
        .order_by(RawRecord.line_number)
        .all()
    )
    assert b"".join(r.raw_line for r in old_rows) == original
    new_rows = (
        db_session.query(RawRecord)
        .filter_by(source_file_id=new.id)
        .order_by(RawRecord.line_number)
        .all()
    )
    assert b"".join(r.raw_line for r in new_rows) == replacement

    anomaly = db_session.query(ParseAnomaly).filter_by(kind="source_diverged").one()
    assert anomaly.detail["reappeared"] is True


def test_divergence_still_detected_after_reappearance_resume(db_session, fixture_tree):
    """A reactivated row is a fully live row: a LATER rewrite must still fire divergence."""
    main, content = _make_gone(db_session, fixture_tree)
    main.path.write_bytes(content)
    _capture_all(db_session, fixture_tree)  # identical restore -> reappearance-resume

    main.path.write_bytes(
        b'{"type":"user","message":{"role":"user","content":"POST-RESTORE"},"uuid":"u-pr1"}\n'
        + content[content.index(b"\n") + 1:]
    )
    _capture_all(db_session, fixture_tree)

    gens = db_session.query(SourceFile).filter_by(path=str(main.path)).all()
    assert {g.status for g in gens} == {"diverged", "active"}
    assert next(g for g in gens if g.status == "active").is_primary
