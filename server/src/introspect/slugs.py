"""Path→slug encoding matching the Claude Code CLI's project-directory naming.

The CLI stores transcripts under ``~/.claude/projects/<slug>/`` where the slug is the
project's absolute cwd with every character outside ``[A-Za-z0-9-]`` replaced by ``-``
(no lowercasing). Census-derived 2026-08-17 from live ``projects`` rows (spec §1):
``/@ai/`` → ``--ai-``, ``project_centipede`` → ``project-centipede``,
``relativityboy.com`` → ``relativityboy-com``. Needed so a NEVER-captured project can be
excluded by path before its first byte lands — the slug must be predictable, not observed.

Known limitation, inherited from the CLI's own scheme: distinct paths can collide to one
slug (``project_x`` / ``project.x``); exclusion is therefore slug-granular, exactly as
granular as the CLI's storage itself.
"""

from __future__ import annotations

import re
from pathlib import Path

_NON_SLUG = re.compile(r"[^A-Za-z0-9-]")


def slug_for_path(path: str | Path) -> str:
    """The CLI's project-dir slug for ``path`` (expanded, absolute, trailing-slash-free)."""
    normalized = Path(path).expanduser().absolute()
    return _NON_SLUG.sub("-", str(normalized))
