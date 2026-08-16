"""Distribute repo-shipped agent skills into the user's ``~/.claude/skills/`` (§16, /skill).

The repo's ``skills/<name>/SKILL.md`` files are TEMPLATES: they carry the
``__INTROSPECT_SERVER_DIR__`` placeholder wherever a command needs this machine's checkout
path (e.g. starting a read-only server), rendered at install time against the actual repo
root. Rendering is a plain ``str.replace`` — never ``str.format`` — because skill bodies
legitimately contain literal braces (jq expressions).

Pure filesystem module: repo root and target dir are always injected, so tests never touch
the real ``~/.claude`` (the same hermetic rule as CrontabIO). Status/install compare the
RENDERED template against the installed bytes, so a machine-specific path difference never
reads as drift on the machine it was rendered for.
"""

from __future__ import annotations

from pathlib import Path

SERVER_DIR_PLACEHOLDER = "__INTROSPECT_SERVER_DIR__"

STATUS_MISSING = "missing"
STATUS_STALE = "stale"
STATUS_CURRENT = "current"


def render_skill(template_text: str, server_dir: Path) -> str:
    """Render a skill template for this machine (placeholder -> actual server dir)."""
    return template_text.replace(SERVER_DIR_PLACEHOLDER, str(server_dir))


def _repo_skill_dirs(repo_root: Path) -> list[Path]:
    skills_dir = repo_root / "skills"
    if not skills_dir.is_dir():
        return []
    return sorted(d for d in skills_dir.iterdir() if (d / "SKILL.md").is_file())


def _rendered(repo_root: Path, skill_dir: Path) -> str:
    template = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    return render_skill(template, repo_root / "server")


def skills_status(repo_root: Path, skills_home: Path) -> dict[str, str]:
    """Per-skill install state: ``missing`` | ``stale`` | ``current``, keyed by skill name."""
    status: dict[str, str] = {}
    for skill_dir in _repo_skill_dirs(repo_root):
        installed = skills_home / skill_dir.name / "SKILL.md"
        if not installed.is_file():
            status[skill_dir.name] = STATUS_MISSING
        elif installed.read_text(encoding="utf-8") != _rendered(repo_root, skill_dir):
            status[skill_dir.name] = STATUS_STALE
        else:
            status[skill_dir.name] = STATUS_CURRENT
    return status


def install_skills(repo_root: Path, skills_home: Path) -> dict[str, str]:
    """Install/update every repo skill into ``skills_home``; idempotent.

    Returns per-skill outcomes: ``installed`` (was missing) | ``updated`` (was stale) |
    ``current`` (already byte-identical; nothing written).
    """
    outcomes: dict[str, str] = {}
    for skill_dir in _repo_skill_dirs(repo_root):
        target = skills_home / skill_dir.name / "SKILL.md"
        rendered = _rendered(repo_root, skill_dir)
        if target.is_file() and target.read_text(encoding="utf-8") == rendered:
            outcomes[skill_dir.name] = STATUS_CURRENT
            continue
        outcome = "updated" if target.is_file() else "installed"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
        outcomes[skill_dir.name] = outcome
    return outcomes
