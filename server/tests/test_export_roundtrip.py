"""Export tests: THE flagship archive guarantee — import -> export == original bytes.

The archive's whole reason to exist is that the bytes it captured can be handed back
exactly. Every test here pins that promise from a different angle: every fixture file,
after a source deletion, after a history divergence, for a no-trailing-newline file, and
for a transcript that only ever had a backup. The two error tests pin the not-found
contract (session absent vs. transcript absent).

Export exposes only the CURRENT reconstruction of a transcript (its primary source file,
or — when none is primary, e.g. a bak-only transcript — the most-complete source file by
record count). Diverged OLD generations are deliberately NOT exportable through this API:
the signature carries no generation, so the flagship guarantee is always about the live
file's bytes. (See task-10-report.md.)
"""

import pytest

from introspect.export import (
    SessionNotFoundError,
    TranscriptNotFoundError,
    export_session_to,
    export_transcript,
)
from introspect.ingest.capture import capture_file, detect_gone
from introspect.ingest.discovery import discover
from tests.fixtures.records import make_user_line
from tests.test_capture import _capture_all


# --- Binding contract (verbatim from task-10-brief) -------------------------------------


def test_roundtrip_every_fixture_file_byte_identical(db_session, fixture_tree):
    """The archive guarantee: import -> export == original bytes, every file."""
    _capture_all(db_session, fixture_tree)
    for f in discover(fixture_tree):
        if f.kind == "backup":
            continue          # backups are non-primary by design
        exported = export_transcript(db_session, f.session_uuid, f.kind, f.agent_hex_id)
        assert exported == f.path.read_bytes(), f.path


def test_roundtrip_survives_source_deletion(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    original = main.path.read_bytes()
    main.path.unlink()
    detect_gone(db_session, list(discover(fixture_tree)))
    assert export_transcript(db_session, main.session_uuid) == original


def test_roundtrip_no_trailing_newline(db_session, tmp_path):
    root = tmp_path / "r"
    slug = root / "-Users-x-proj"
    slug.mkdir(parents=True)
    p = slug / "aaaaaaaa-1111-2222-3333-444444444444.jsonl"
    content = make_user_line(text="one") + make_user_line(text="two").rstrip(b"\n")
    p.write_bytes(content)
    for f in discover(root):
        capture_file(db_session, f)
    assert export_transcript(db_session, "aaaaaaaa-1111-2222-3333-444444444444") == content


def test_export_unknown_session_raises(db_session):
    with pytest.raises(SessionNotFoundError):
        export_transcript(db_session, "no-such-uuid")


# --- Additional required tests (Opus review B1 + M5) ------------------------------------


def test_roundtrip_after_divergence_exports_new_generation(db_session, fixture_tree):
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    content = main.path.read_bytes()
    rewritten = b'{"type":"user","message":{"role":"user","content":"REWRITTEN"},"uuid":"u-new1"}\n' + content[content.index(b"\n") + 1:]
    main.path.write_bytes(rewritten)
    _capture_all(db_session, fixture_tree)
    assert export_transcript(db_session, main.session_uuid) == rewritten


def test_bak_only_transcript_still_exports(db_session, tmp_path):
    root = tmp_path / "r"
    slug = root / "-Users-x-proj"
    slug.mkdir(parents=True)
    content = make_user_line(text="only the backup survived")
    (slug / "cccccccc-1111-2222-3333-444444444444.jsonl.bak-1700000000").write_bytes(content)
    for f in discover(root):
        capture_file(db_session, f)
    assert export_transcript(db_session, "cccccccc-1111-2222-3333-444444444444") == content


# --- Coverage the brief's six leave open (signature + error taxonomy) -------------------


def test_export_session_to_writes_bytes_and_returns_count(db_session, fixture_tree, tmp_path):
    """export_session_to streams the main transcript to disk and returns the byte count."""
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    original = main.path.read_bytes()
    out = tmp_path / "exported.jsonl"

    n = export_session_to(db_session, main.session_uuid, out)

    assert out.read_bytes() == original
    assert n == len(original)


def test_export_known_session_unknown_transcript_raises(db_session, fixture_tree):
    """A real session but a kind/agent it never had is TranscriptNotFound, not SessionNotFound."""
    _capture_all(db_session, fixture_tree)
    main = next(f for f in discover(fixture_tree) if f.kind == "main")
    with pytest.raises(TranscriptNotFoundError):
        export_transcript(db_session, main.session_uuid, "subagent", "deadbeef")
