"""Skill distribution (§16 amendment 2026-08-16): repo `skills/` templates rendered into
the user's `~/.claude/skills/`. All paths injected -- no test touches the real home."""

from __future__ import annotations

from pathlib import Path

from introspect.skills import install_skills, render_skill, skills_status

# Carries the placeholder AND literal jq-style braces: rendering must substitute the one
# and preserve the other (why render is .replace, never str.format).
TEMPLATE = (
    "---\nname: t\n---\n"
    "Start: cd __INTROSPECT_SERVER_DIR__ && uv run introspect serve &\n"
    "Shape: jq '{total, hits}'\n"
)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    skill_dir = root / "skills" / "recalling-past-sessions"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(TEMPLATE, encoding="utf-8")
    return root


def test_render_substitutes_server_dir_and_preserves_braces() -> None:
    out = render_skill(TEMPLATE, Path("/x/checkout/server"))
    assert "cd /x/checkout/server && uv run introspect serve &" in out
    assert "__INTROSPECT_SERVER_DIR__" not in out
    assert "jq '{total, hits}'" in out  # literal braces survive


def test_status_missing_then_current_then_stale(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    home = tmp_path / "skills-home"
    assert skills_status(root, home) == {"recalling-past-sessions": "missing"}
    install_skills(root, home)
    assert skills_status(root, home) == {"recalling-past-sessions": "current"}
    skill_md = root / "skills" / "recalling-past-sessions" / "SKILL.md"
    skill_md.write_text(TEMPLATE + "new guidance\n", encoding="utf-8")
    assert skills_status(root, home) == {"recalling-past-sessions": "stale"}


def test_install_writes_rendered_then_updates_then_noops(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    home = tmp_path / "skills-home"
    assert install_skills(root, home) == {"recalling-past-sessions": "installed"}
    body = (home / "recalling-past-sessions" / "SKILL.md").read_text(encoding="utf-8")
    assert str(root / "server") in body
    assert "__INTROSPECT_SERVER_DIR__" not in body

    skill_md = root / "skills" / "recalling-past-sessions" / "SKILL.md"
    skill_md.write_text(TEMPLATE + "new guidance\n", encoding="utf-8")
    assert install_skills(root, home) == {"recalling-past-sessions": "updated"}
    assert install_skills(root, home) == {"recalling-past-sessions": "current"}


def test_no_skills_dir_reports_empty(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert skills_status(root, tmp_path / "h") == {}
    assert install_skills(root, tmp_path / "h") == {}
