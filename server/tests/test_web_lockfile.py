"""Closure lint for web/package-lock.json — a real-repo-artifact test.

npm versions disagree about what belongs in a lockfile: newer npm (observed:
11.6.1) neither materializes nor validates required peer dependencies of
optional platform packages, while older npm does both — so a lock authored by
the newer npm makes the older one fail ``npm ci`` with "Missing: <pkg> from
lock file". This broke the first foreign-machine install of this repo twice
(2026-08-09): ``@napi-rs/wasm-runtime`` requires peers ``@emnapi/core`` and
``@emnapi/runtime``, and the committed lock resolved neither (then only one).

The check mirrors npm's actual resolution: a reference from an entry resolves
by walking UP the tree (own nested node_modules first, then each ancestor's,
then the root), and required peers (those not marked optional in
``peerDependenciesMeta``) count as references. Like
``test_repo_changelog_conforms``, this test deliberately reads the real file:
it lints the committed artifact so incompleteness fails at authoring time on
the machine that pruned the lock, not at install time on the machine that
needed it. If it fails: regenerate with ``npm install`` on the strictest npm
in the family and commit that lock.
"""

import json
from pathlib import Path


def _resolution_points(referrer_key: str, name: str) -> list[str]:
    """Lock keys where npm would look for ``name`` from ``referrer_key``, nearest first."""
    points = [f"{referrer_key}/node_modules/{name}"] if referrer_key else []
    prefix = referrer_key
    while prefix:
        prefix = prefix.rsplit("/node_modules/", 1)[0] if "/node_modules/" in prefix else ""
        base = f"{prefix}/node_modules/{name}" if prefix else f"node_modules/{name}"
        points.append(base)
    if not referrer_key:
        points.append(f"node_modules/{name}")
    return points


def test_web_lockfile_resolves_all_references() -> None:
    lock_path = Path(__file__).resolve().parents[2] / "web" / "package-lock.json"
    assert lock_path.is_file(), "web/package-lock.json missing from the repo"
    packages = json.loads(lock_path.read_text(encoding="utf-8"))["packages"]

    unresolved: dict[str, str] = {}
    for entry_key, entry in packages.items():
        peer_meta = entry.get("peerDependenciesMeta", {})
        referenced = dict(entry.get("dependencies", {}))
        referenced.update(entry.get("optionalDependencies", {}))
        for peer in entry.get("peerDependencies", {}):
            if not peer_meta.get(peer, {}).get("optional", False):
                referenced[peer] = "(required peer)"
        for name in referenced:
            if not any(point in packages for point in _resolution_points(entry_key, name)):
                unresolved[f"{entry_key or '(root)'} -> {name}"] = referenced[name]

    assert not unresolved, (
        "lockfile references that npm's walk-up resolution cannot satisfy "
        "(regenerate with `npm install` on the strictest npm in the family): "
        f"{unresolved}"
    )
