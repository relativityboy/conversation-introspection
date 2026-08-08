#!/usr/bin/env bash
#
# update.sh -- pull the latest release and re-converge (deps, web build). PROMPTLESS BY
# DESIGN: consent lives in the callers (`/update`'s confirm in the TUI, `introspect
# update`'s [y/N], or you deciding to run this). Like install.sh it is an ORCHESTRATOR:
# git and install.sh do the work; this script sequences them and reports honestly.
#
# It never stashes, merges, or resets. A dirty tree or a diverged branch is YOUR
# decision; this script stops and says exactly what it found.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -t 1 ]; then
  C_BOLD=$'\033[1m'; C_RED=$'\033[31m'; C_OFF=$'\033[0m'
else
  C_BOLD=''; C_RED=''; C_OFF=''
fi
log_step() { printf '%s==>%s %s\n' "$C_BOLD" "$C_OFF" "$*"; }
log_err()  { printf '%serror%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; }

current_version() {
  sed -n 's/^## \([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)[[:space:]].*/\1/p' \
    "$REPO_ROOT/CHANGELOG.md" 2>/dev/null | head -n 1
}

log_step "Preflight"
if ! command -v git >/dev/null 2>&1; then
  log_err "git is required."; exit 1
fi
if ! git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log_err "not a git checkout: $REPO_ROOT -- update.sh only works from a cloned repo."; exit 1
fi
dirty="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)"
if [ -n "$dirty" ]; then
  log_err "working tree has uncommitted changes to tracked files:"
  printf '%s\n' "$dirty" >&2
  log_err "commit or stash them yourself, then re-run -- update.sh never stashes."
  exit 1
fi
if ! upstream="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)"; then
  log_err "the current branch has no upstream -- set one (git branch --set-upstream-to=...) or pull manually."
  exit 1
fi

old_version="$(current_version)"
old_head="$(git -C "$REPO_ROOT" rev-parse HEAD)"

log_step "Pull ($upstream, fast-forward only)"
if ! git -C "$REPO_ROOT" pull --ff-only; then
  log_err "pull failed. If the branch has diverged from $upstream, resolve it yourself -- update.sh never merges."
  exit 1
fi

log_step "Re-converge (./install.sh --yes --skip-import)"
if ! "$REPO_ROOT/install.sh" --yes --skip-import; then
  # install.sh already printed which step failed and why.
  log_err "update incomplete -- fix the problem above and re-run ./update.sh (every step re-converges)."
  exit 1
fi

new_version="$(current_version)"
new_head="$(git -C "$REPO_ROOT" rev-parse HEAD)"
if [ "$old_head" = "$new_head" ]; then
  log_step "already up to date (${new_version:-unknown})"
else
  log_step "updated ${old_version:-unknown} -> ${new_version:-unknown}"
fi
