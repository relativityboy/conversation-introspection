from introspect.ingest.discovery import DiscoveredFile, discover
from tests.conftest import AGENT_HEX_ID, TOTAL_FIXTURE_LINES


# --- Required representative set (verbatim from task-4 brief) ---------------------------


def test_discovers_main_subagent_backup(fixture_tree):
    found = list(discover(fixture_tree))
    kinds = sorted(f.kind for f in found)
    assert kinds == ["backup", "main", "main", "main", "subagent"]


def test_subagent_carries_meta_and_parent_session(fixture_tree):
    sub = next(f for f in discover(fixture_tree) if f.kind == "subagent")
    assert sub.agent_hex_id == "abc123"
    assert sub.agent_meta.tool_use_id == "toolu_fixture01"
    assert sub.session_uuid in {f.session_uuid for f in discover(fixture_tree) if f.kind == "main"}


def test_missing_meta_json_tolerated(fixture_tree):
    (next(fixture_tree.glob("*/*/subagents/*.meta.json"))).unlink()
    sub = next(f for f in discover(fixture_tree) if f.kind == "subagent")
    assert sub.agent_meta is None


def test_backup_ties_to_same_session(fixture_tree):
    bak = next(f for f in discover(fixture_tree) if f.kind == "backup")
    assert bak.session_uuid in {f.session_uuid for f in discover(fixture_tree) if f.kind == "main"}


# --- Self-review coverage (skip rules, sorting, meta robustness, fixture accounting) -----


def test_all_results_are_discovered_file_instances(fixture_tree):
    found = list(discover(fixture_tree))
    assert len(found) == 5
    assert all(isinstance(f, DiscoveredFile) for f in found)


def test_output_is_sorted_by_path(fixture_tree):
    found = list(discover(fixture_tree))
    paths = [f.path for f in found]
    assert paths == sorted(paths)


def test_discover_is_deterministic_across_calls(fixture_tree):
    first = [(f.path, f.kind) for f in discover(fixture_tree)]
    second = [(f.path, f.kind) for f in discover(fixture_tree)]
    assert first == second


def test_main_files_report_correct_slug_and_session_uuid(fixture_tree):
    mains = {f.session_uuid: f.project_slug for f in discover(fixture_tree) if f.kind == "main"}
    assert mains == {
        "11111111-1111-1111-1111-111111111111": "-Users-x-proj",
        "22222222-2222-2222-2222-222222222222": "-Users-x-proj",
        "33333333-3333-3333-3333-333333333333": "-Users-x-proj2",
    }


def test_subagent_meta_fields_fully_populated(fixture_tree):
    sub = next(f for f in discover(fixture_tree) if f.kind == "subagent")
    assert sub.agent_hex_id == AGENT_HEX_ID
    assert sub.agent_meta.agent_type == "Explore"
    assert sub.agent_meta.description
    assert sub.agent_meta.tool_use_id == "toolu_fixture01"


def test_corrupt_meta_json_tolerated(fixture_tree):
    meta_path = next(fixture_tree.glob("*/*/subagents/*.meta.json"))
    meta_path.write_text("{not valid json")
    sub = next(f for f in discover(fixture_tree) if f.kind == "subagent")
    assert sub.agent_meta is None


def test_unrelated_files_are_skipped_silently(fixture_tree):
    proj1 = fixture_tree / "-Users-x-proj"
    (proj1 / ".DS_Store").write_bytes(b"junk")
    (proj1 / "not-a-uuid.jsonl").write_bytes(b"{}\n")
    (proj1 / "11111111-1111-1111-1111-111111111111.jsonl.stray").write_bytes(b"{}\n")
    found = list(discover(fixture_tree))
    kinds = sorted(f.kind for f in found)
    assert kinds == ["backup", "main", "main", "main", "subagent"]


def test_total_fixture_lines_matches_discovered_main_and_subagent_content(fixture_tree):
    total = 0
    for f in discover(fixture_tree):
        if f.kind in ("main", "subagent"):
            total += sum(1 for _ in f.path.open("rb"))
    assert total == TOTAL_FIXTURE_LINES


def test_backup_content_is_prefix_of_its_main_file(fixture_tree):
    bak = next(f for f in discover(fixture_tree) if f.kind == "backup")
    main = next(
        f for f in discover(fixture_tree) if f.kind == "main" and f.session_uuid == bak.session_uuid
    )
    bak_lines = bak.path.read_bytes().splitlines(keepends=True)
    main_lines = main.path.read_bytes().splitlines(keepends=True)
    assert bak_lines == main_lines[: len(bak_lines)]
    assert len(bak_lines) == 2
