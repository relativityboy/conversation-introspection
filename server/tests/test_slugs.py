"""Path→slug encoding (spec 2026-08-17 §1): must match the Claude Code CLI's project-dir
naming. Every pair below is census-derived from real archive `projects` rows — if the CLI's
scheme ever drifts, update the census first, then these pins."""

from __future__ import annotations

from introspect.slugs import slug_for_path


def test_slug_census_pairs() -> None:
    assert (
        slug_for_path("/Users/donovan/projects/@ai/conversation-introspection")
        == "-Users-donovan-projects--ai-conversation-introspection"
    )
    assert (
        slug_for_path("/Users/donovan/projects/@ai/project_centipede")
        == "-Users-donovan-projects--ai-project-centipede"
    )
    assert (
        slug_for_path("/Users/donovan/projects/@ai/relativityboy.com")
        == "-Users-donovan-projects--ai-relativityboy-com"
    )
    # existing hyphens survive; no lowercasing
    assert (
        slug_for_path("/Users/donovan/projects/@ai/smart-little-library")
        == "-Users-donovan-projects--ai-smart-little-library"
    )


def test_slug_normalizes_trailing_slash() -> None:
    assert slug_for_path("/tmp/@work/secret_proj/") == slug_for_path("/tmp/@work/secret_proj")
