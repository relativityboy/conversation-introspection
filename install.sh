#!/usr/bin/env bash
#
# install.sh -- one-command setup for conversation-introspection.
#
# This is an ORCHESTRATOR: it does no install work itself. Every step shells out to a tested
# tool (uv, npm, the introspect CLI) and reports honestly what happened. Flow:
#
#   preflight  -- verify OS (macOS/Linux) and required tools (git, curl, node, npm); OFFER to
#                 install uv via its official installer if it is missing (consent-gated, never
#                 silent).
#   step 1     -- cd server && uv sync            (create the Python venv + deps)
#   step 2     -- cd web    && npm ci             (install the web build deps)
#   step 3     -- cd web    && npm run build      (build the reading-room UI into web/dist)
#   step 4     -- cd server && uv run introspect import   (seed the archive; empty source is ok)
#   done       -- print next steps (introspect tui, /cron install, /start-web).
#
# Flags:
#   --yes, -y        non-interactive: accept every prompt (currently: the uv-install consent).
#   --skip-import    do not run the first import (step 4).
#   --help, -h       show usage and exit.
#
# ALWAYS-RUN (design):
#   Every step runs on every invocation. The TOOLS are the convergence layer -- uv sync, npm ci,
#   npm run build and the importer each reconcile their own state and are safe to repeat -- so the
#   installer's job is to run them and report honestly, never to guess whether they still need it.
#
#   This replaces an earlier design that skipped a step whenever the artifact it produces already
#   existed (server/.venv, web/node_modules, web/dist/index.html, the archive db). That gate was
#   wrong in two independent directions:
#     * presence != success. `uv sync` creates .venv BEFORE a dependency build can fail; `npm ci`
#       leaves a partial node_modules when an install dies midway; the importer commits its
#       schema-version stamp and ImportRun row before any capture work. Each leaves its artifact
#       behind on failure -- so the next run skipped precisely the step that had not finished.
#     * presence != current. A stale web/dist survives `git pull`, so the installer silently kept
#       serving the OLD UI, and an updated uv.lock / package-lock.json was ignored outright
#       whenever the deps directory happened to exist.
#   The old rationale ("a stamp file would lie") was half right: a stamp file does lie -- and so
#   does an artifact's mere presence, which just looks more physical. The honest signal is the
#   tool's own convergence check, which is the thing the gating routed around.
#
#   Accepted cost, measured rather than guessed: `npm ci` converges by wiping and reinstalling
#   node_modules, so it does real work every time. On a warm npm cache a no-change re-run of ALL
#   steps measured ~3s end to end (uv sync 9ms, npm ci 2s, vite build 0.4s). Comparing lockfile
#   mtimes would shave that and would reintroduce a proxy-for-truth -- the same class of mistake
#   this design removes. Cheap enough that the tradeoff is not close.
#
# FAILURE HONESTY:
#   A failing step prints WHICH step failed, the tail of the tool's captured output (the WHY), and
#   that re-running ./install.sh is safe: every step re-converges, so a fixed environment reaches a
#   complete install. Preflight failures happen before any step, so a missing prerequisite never
#   leaves a half-built tree.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$REPO_ROOT/server"
WEB_DIR="$REPO_ROOT/web"
ASSUME_YES=0
SKIP_IMPORT=0

# --- Presentation -------------------------------------------------------------------------
# Colour only when stdout is a terminal; tests (and pipes) get clean, greppable text.
if [ -t 1 ]; then
  C_BOLD=$'\033[1m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_BOLD=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_DIM=''; C_OFF=''
fi

log_info() { printf '%s\n' "$*"; }
log_step() { printf '%s==>%s %s\n' "$C_BOLD" "$C_OFF" "$*"; }
log_ok()   { printf '%s  ok%s   %s\n' "$C_GREEN" "$C_OFF" "$*"; }
log_skip() { printf '%s  skip%s %s\n' "$C_DIM" "$C_OFF" "$*"; }
log_warn() { printf '%s  warn%s %s\n' "$C_YELLOW" "$C_OFF" "$*"; }
log_err()  { printf '%serror%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; }

usage() {
  cat <<EOF
${C_BOLD}install.sh${C_OFF} -- set up conversation-introspection (deps, web build, first import).

Usage: ./install.sh [--yes] [--skip-import]

  --yes, -y       Non-interactive: accept every prompt (e.g. installing uv).
  --skip-import   Skip the archive import.
  --help, -h      Show this help.

Re-running is safe, and is how you update: every step re-converges, so a re-run
repairs a partial install and picks up whatever you pulled (new deps, new UI sources).
EOF
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --yes|-y) ASSUME_YES=1 ;;
      --skip-import) SKIP_IMPORT=1 ;;
      --help|-h) usage; exit 0 ;;
      *) log_err "unknown option: $1"; usage >&2; exit 2 ;;
    esac
    shift
  done
}

# --- Preflight ----------------------------------------------------------------------------

# have <bin> -- true iff the executable is resolvable on PATH (a shell builtin; never runs it).
have() { command -v "$1" >/dev/null 2>&1; }

check_required_tool() {
  # $1=binary  $2=label  $3=install hint. Returns 0 if present, else prints a hint and returns 1.
  if have "$1"; then
    log_ok "$2 found"
    return 0
  fi
  log_err "missing: $2 -- $3"
  return 1
}

preflight() {
  log_step "Preflight checks"

  local os; os="$(uname -s)"
  case "$os" in
    Darwin|Linux) log_ok "OS: $os" ;;
    *) log_err "unsupported OS '$os' -- this installer supports macOS (Darwin) and Linux only."; exit 1 ;;
  esac

  # Report EVERY missing prerequisite at once, then stop -- friendlier than failing one at a time.
  local missing=0
  check_required_tool git  "git"  "install from https://git-scm.com/downloads (macOS: xcode-select --install)" || missing=1
  check_required_tool curl "curl" "preinstalled on macOS; 'apt-get install curl' or 'dnf install curl' on Linux" || missing=1
  check_required_tool node "node" "install Node.js from https://nodejs.org (or: brew install node / your distro's nodejs package)" || missing=1
  check_required_tool npm  "npm"  "ships with Node.js -- installing Node installs npm" || missing=1
  if [ "$missing" -ne 0 ]; then
    log_err "missing prerequisites (above). Install them, then re-run ./install.sh -- nothing was changed."
    exit 1
  fi

  # uv is special: there is a canonical one-line installer for it, so we OFFER to run it.
  if have uv; then
    log_ok "uv found ($(command -v uv))"
  else
    offer_uv_install
  fi
}

offer_uv_install() {
  log_warn "uv (the Python package manager this project uses) is not installed."
  if [ "$ASSUME_YES" -ne 1 ]; then
    printf '      Install uv now via its official installer (curl -LsSf https://astral.sh/uv/install.sh | sh)? [y/N] '
    local reply=""
    read -r reply || reply=""
    case "$reply" in
      y|Y|yes|YES) : ;;
      *) log_err "uv is required. Install it (https://docs.astral.sh/uv/) and re-run ./install.sh -- nothing was changed."; exit 1 ;;
    esac
  else
    log_info "      --yes given: installing uv via its official installer."
  fi

  log_step "Install uv (official installer)"
  if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
    log_err "uv installation failed. Install it manually (https://docs.astral.sh/uv/) and re-run ./install.sh."
    exit 1
  fi
  # uv's installer drops the binary in ~/.local/bin (or ~/.cargo/bin); make it visible to THIS run.
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  hash -r 2>/dev/null || true
  if ! have uv; then
    log_err "uv was installed but is not on PATH yet. Open a new shell (or add ~/.local/bin to PATH) and re-run ./install.sh."
    exit 1
  fi
  log_ok "uv installed ($(command -v uv))"
}

# --- Steps --------------------------------------------------------------------------------

# run_step "<name>" "<dir>" cmd args...
#   Runs the command in <dir>, streaming its output live while capturing it. On failure, prints
#   the step name, the tail of the captured output, and resume guidance, then exits nonzero.
run_step() {
  local name="$1" dir="$2"; shift 2
  local log; log="$(mktemp -t introspect-install.XXXXXX)"
  log_step "$name"
  if ( cd "$dir" && "$@" ) 2>&1 | tee "$log"; then
    rm -f "$log"
    return 0
  fi
  echo >&2
  log_err "step failed: $name"
  printf '%s  --- last output from the failed step ---%s\n' "$C_DIM" "$C_OFF" >&2
  tail -n 20 "$log" 2>/dev/null | sed 's/^/  /' >&2
  printf '%s  ----------------------------------------%s\n' "$C_DIM" "$C_OFF" >&2
  log_err "fix the problem above, then re-run ./install.sh -- every step re-converges, so the re-run completes the install."
  rm -f "$log"
  exit 1
}

# The three build steps are plain run_step calls -- see ALWAYS-RUN above for why there is no
# "already done" branch. Only the import has a branch, because --skip-import is a user choice.
step_import() {
  if [ "$SKIP_IMPORT" -eq 1 ]; then
    log_skip "Import -- skipped (--skip-import)"
    return
  fi
  run_step "Import transcripts (cd server && uv run introspect import)" "$SERVER_DIR" uv run introspect import
}

next_steps() {
  echo
  log_step "Setup complete."
  cat <<EOF

Next steps:

  ${C_BOLD}1. Keep your archive protected on a schedule (recommended)${C_OFF}
       cd server && uv run introspect tui
     then, inside the TUI, type:  ${C_BOLD}/cron install${C_OFF}
     (this schedules an import every 15 minutes so nothing ages out before it's captured.
      Prefer the CLI? run:  uv run introspect cron install)

  ${C_BOLD}2. Read your conversations${C_OFF}
       inside the TUI, type:  ${C_BOLD}/start-web${C_OFF}   -> opens http://127.0.0.1:8765
     (or run it standalone:  uv run introspect serve)

  Type  /help  in the TUI for every command, or see the README and docs/user/ guide.
EOF
}

# --- Main ---------------------------------------------------------------------------------

main() {
  parse_args "$@"
  printf '%sconversation-introspection installer%s\n' "$C_BOLD" "$C_OFF"
  log_info "repo: $REPO_ROOT"
  echo
  preflight
  echo
  log_step "Installing"
  run_step "Install Python deps (cd server && uv sync)" "$SERVER_DIR" uv sync
  run_step "Install web deps (cd web && npm ci)" "$WEB_DIR" npm ci
  run_step "Build the web UI (cd web && npm run build)" "$WEB_DIR" npm run build
  step_import
  next_steps
}

main "$@"
