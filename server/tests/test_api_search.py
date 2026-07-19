"""Search endpoint (Task P2-6): GET /api/v1/search.

The endpoint has two response shapes selected by ``scope`` -- see
``introspect.api.routes.search`` for the binding pagination-is-over-hits semantics of
``scope=global`` grouping. These tests build small custom transcript trees (rather than the
shared pinned ``fixture_tree``) whenever a test needs to control cross-session rank or hit
count precisely; the pinned fixture (session 1's unique "horizon" / "still water" phrases) is
reused for the shape/error-path tests that don't care about ranking.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from introspect.api import create_app
from introspect.ingest.capture import capture_file
from introspect.ingest.discovery import discover
from introspect.search import get_search_index
from tests.conftest import AGENT_HEX_ID, SESSION_UUID_1
from tests.fixtures.records import make_assistant_line, make_session_file, make_user_line


def _capture_and_index(db: Session, root: Path) -> None:
    for f in discover(root):
        capture_file(db, f)
    db.commit()
    get_search_index().rebuild(db)
    db.commit()


def _write_session(root: Path, project_slug: str, session_uuid: str, texts: list[str]) -> None:
    """Write one session file whose alternating user/assistant lines carry ``texts`` bodies."""
    proj = root / project_slug
    proj.mkdir(parents=True, exist_ok=True)
    lines = [
        (make_user_line if i % 2 == 0 else make_assistant_line)(text=t, sessionId=session_uuid)
        for i, t in enumerate(texts)
    ]
    (proj / f"{session_uuid}.jsonl").write_bytes(make_session_file(lines))


@pytest.fixture
def client(db_session: Session, fixture_tree: Path, tmp_path: Path) -> TestClient:
    """App over the pinned fixture tree, indexed, sharing ``db_session``'s DB file."""
    _capture_and_index(db_session, fixture_tree)
    return TestClient(create_app(db_path=tmp_path / "archive.db"))


# --- Global scope: grouping, best-rank-first ordering, per-group cap --------------------

# NOTE(claude): the weak session's uuid ('aaaa...'), project slug ('-Users-x-1-weak'), and
# discovery/insertion order (written first below) all sort/land BEFORE the strong session's --
# the opposite of its bm25 rank. Only bm25 rank puts strong first in the response, so if a
# regression ever swapped the ORDER BY for insertion/rowid order, this test would catch it
# instead of passing by coincidence.
GROUP_STRONG_SESSION = "ffffffff-1111-4111-8111-111111111111"  # 1 hit, dense term -> best rank
GROUP_WEAK_SESSION = "aaaaaaaa-1111-4111-8111-111111111111"  # 7 hits, sparse term -> capped


def test_global_search_orders_groups_by_best_rank_and_caps_at_five(
    tmp_path: Path, db_session: Session
) -> None:
    root = tmp_path / "search_tree"
    # Weak session written (and thus discovered/captured) FIRST, into a project slug that
    # sorts first too -- both point away from its actual rank so only bm25 explains the order.
    weak_texts = [
        f"filler padding tokens surrounding a lone zephyr mention number {i} more words here"
        for i in range(7)
    ]
    _write_session(root, "-Users-x-1-weak", GROUP_WEAK_SESSION, weak_texts)
    # Strong session: a single, densely-matching message (short doc, repeated term) so its
    # bm25 rank is unambiguously the best, despite sorting/inserting after the weak session.
    _write_session(
        root, "-Users-x-2-strong", GROUP_STRONG_SESSION, ["zephyr zephyr zephyr strong signal"]
    )
    _capture_and_index(db_session, root)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    body = client.get("/api/v1/search", params={"q": "zephyr"}).json()

    assert body["total"] == 8  # 1 strong + 7 weak, pre-cap
    assert [g["session"]["session_uuid"] for g in body["groups"]] == [
        GROUP_STRONG_SESSION,
        GROUP_WEAK_SESSION,
    ]

    strong_group = body["groups"][0]
    assert len(strong_group["hits"]) == 1
    assert strong_group["has_more"] is False

    weak_group = body["groups"][1]
    assert len(weak_group["hits"]) == 5  # capped
    assert weak_group["has_more"] is True


# --- Session scope: flat, paged, filtered to one session ---------------------------------

LUMEN_SESSION = "cccccccc-1111-4111-8111-111111111111"
LUMEN_DECOY_SESSION = "dddddddd-1111-4111-8111-111111111111"


def test_session_scope_flat_paged_and_filtered_to_one_session(
    tmp_path: Path, db_session: Session
) -> None:
    root = tmp_path / "search_tree"
    _write_session(
        root, "-Users-x-c", LUMEN_SESSION, [f"lumen candidate message number {i}" for i in range(4)]
    )
    _write_session(root, "-Users-x-d", LUMEN_DECOY_SESSION, ["a decoy lumen hit"])
    _capture_and_index(db_session, root)
    client = TestClient(create_app(db_path=tmp_path / "archive.db"))

    everything = client.get(
        "/api/v1/search", params={"q": "lumen", "scope": "session", "session": LUMEN_SESSION}
    ).json()
    assert "items" in everything and "groups" not in everything
    assert everything["total"] == 4  # decoy session's hit is excluded

    page1 = client.get(
        "/api/v1/search",
        params={
            "q": "lumen", "scope": "session", "session": LUMEN_SESSION, "limit": 2, "offset": 0
        },
    ).json()
    page2 = client.get(
        "/api/v1/search",
        params={
            "q": "lumen", "scope": "session", "session": LUMEN_SESSION, "limit": 2, "offset": 2
        },
    ).json()
    assert page1["total"] == page2["total"] == 4
    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 2

    ids_1 = {i["record_uuid"] for i in page1["items"]}
    ids_2 = {i["record_uuid"] for i in page2["items"]}
    assert ids_1.isdisjoint(ids_2)  # the two pages cover disjoint hits
    assert ids_1 | ids_2 == {i["record_uuid"] for i in everything["items"]}


# --- Error paths: both are 422 problem responses, never a 500 ----------------------------


def test_session_scope_without_session_param_is_422_problem(client: TestClient) -> None:
    resp = client.get("/api/v1/search", params={"q": "horizon", "scope": "session"})
    assert resp.status_code == 422
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["status"] == 422


@pytest.mark.parametrize("q", ["", "   "])
def test_empty_or_whitespace_query_is_422_problem(client: TestClient, q: str) -> None:
    resp = client.get("/api/v1/search", params={"q": q})
    assert resp.status_code == 422
    body = resp.json()
    assert set(body) == {"status", "title", "detail"}
    assert body["status"] == 422


# Same evil strings as test_search_fts5.test_sanitize_never_raises, minus "" and "   " --
# those two are the dedicated empty/whitespace 422 cases above, not 200-zero-hits cases.
@pytest.mark.parametrize(
    "evil",
    ['"unbalanced', "a AND OR", "x NEAR/3 y", "(paren", "*star", "col:on", "-minus"],
)
def test_evil_query_never_500s_and_returns_zero_hits(client: TestClient, evil: str) -> None:
    resp = client.get("/api/v1/search", params={"q": evil})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["groups"] == []


# --- Shape: snippet highlighting + HitOut field contract ----------------------------------


def test_snippet_highlighted_and_hit_out_shape(client: TestClient) -> None:
    body = client.get("/api/v1/search", params={"q": "horizon"}).json()
    assert body["total"] == 1  # "horizon" is unique to session 1's single user line

    group = body["groups"][0]
    assert group["session"]["session_uuid"] == SESSION_UUID_1
    assert group["has_more"] is False

    hit = group["hits"][0]
    assert set(hit) == {
        "record_uuid", "transcript_id", "block_index", "block_kind", "snippet", "timestamp",
        "agent_hex_id",
    }
    assert "<mark>" in hit["snippet"]
    assert hit["block_kind"] == "text"
    # "horizon" lives in session 1's MAIN transcript, so this hit routes the main path (no hex).
    assert hit["agent_hex_id"] is None


# --- Deep-link seam: hits carry the subagent hex so the reader routes to /a/{hex}/ (P3-10) --


def test_hit_agent_hex_id_distinguishes_subagent_from_main_transcript(client: TestClient) -> None:
    """A hit in a SUBAGENT transcript carries its hex; a MAIN-transcript hit carries null.

    Without this the reader deep-links every hit to the main-conversation path, which then
    fetches the main transcript with a foreign record uuid and 404s (the cross-layer bug).
    """
    # "cormorant" is unique to the fixture's subagent transcript user line.
    sub = client.get("/api/v1/search", params={"q": "cormorant"}).json()
    assert sub["total"] == 1
    sub_group = sub["groups"][0]
    assert sub_group["session"]["session_uuid"] == SESSION_UUID_1  # subagent belongs to session 1
    sub_hit = sub_group["hits"][0]
    assert sub_hit["agent_hex_id"] == AGENT_HEX_ID

    # "horizon" is unique to session 1's MAIN transcript -> the hit routes the main path.
    main_hit = client.get("/api/v1/search", params={"q": "horizon"}).json()["groups"][0]["hits"][0]
    assert main_hit["agent_hex_id"] is None
