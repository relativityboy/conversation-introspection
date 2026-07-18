"""Runtime configuration: filesystem locations for the archive DB and source root.

Precedence for every setting: explicit argument > environment variable > default.
"""

import os
from pathlib import Path

DEFAULT_DB = Path.home() / ".conversation-introspection" / "archive.db"
DEFAULT_SOURCE_ROOT = Path.home() / ".claude" / "projects"


def db_path(cli_value: str | None = None) -> Path:
    """Resolve the SQLite archive path, creating its parent directory."""
    p = Path(cli_value or os.environ.get("INTROSPECT_DB") or DEFAULT_DB)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def source_root(cli_value: str | None = None) -> Path:
    """Resolve the root directory that holds Claude Code project transcripts."""
    return Path(cli_value or os.environ.get("INTROSPECT_SOURCE_ROOT") or DEFAULT_SOURCE_ROOT)
