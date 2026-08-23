---
name: session-name
description: Use when the user says `session-name <name>` or `/session-name <name>` — names the CURRENT Claude session in the conversation-introspection archive (the title shown in its TUI and reading room). Not for looking anything up; that is recalling-past-sessions.
---

# Naming this session in the archive

The user gives the name; you give it to the archive server, which stores it as this
session's user title. If the archive has not captured this session yet (the hourly import
has not run since it started), the server imports first, then names it — one call. You
never start the server, never import by hand, never guess the session id.

The name is everything after `session-name`, trimmed. Empty → ask for one and stop.
Naming again overwrites. This sets the archive's title only; Claude Code's own session
list is untouched.

## 1. Identify this session

```bash
echo "$CLAUDE_CODE_SESSION_ID"
```

Claude Code sets it in your shell; it is the uuid of the transcript being written right
now. Unset or empty → tell the user you can't identify this session (the variable is
missing), and stop.

## 2. Check the server is up — and new enough

```bash
curl -s --max-time 2 http://127.0.0.1:8765/api/v1/status | jq -r .version
```

- **No response:** tell the user the archive server isn't up, with the start command —
  `cd __INTROSPECT_SERVER_DIR__ && uv run introspect serve --port 8765` (or the TUI's
  `/web start`) — and stop. **Do not start it yourself**; the user asked to be told.
- **Version below 1.8.0:** the running process predates session naming (it reports the
  version it was started with). Say so, say it needs a restart, and stop.

## 3. One call: name it, importing first if needed

```bash
NAME='<the name>'
out=$(curl -s --max-time 180 -w '\n%{http_code}' -X PUT \
  "http://127.0.0.1:8765/api/v1/sessions/$CLAUDE_CODE_SESSION_ID/title?import_if_missing=true" \
  -H 'content-type: application/json' \
  --data "$(jq -nc --arg t "$NAME" '{title:$t}')")
code=${out##*$'\n'}; body=${out%$'\n'*}; echo "$code $body"
```

`jq -nc --arg` is what makes quotes and backslashes in the name safe. The generous
`--max-time` covers the import the server may run inside this request (seconds normally;
it also waits up to 30s if cron's import holds the lock).

## 4. Report, one sentence

- `204` — named. Say the name back: *Named this session "…" in the archive.*
- `404` — still not in the archive after the import; relay the server's `detail` (it names
  the run and the likely reasons: excluded project, transcript not yet on disk).
- `409` — an import held the lock the whole time the server waited; say to try again in a
  minute.
- `422` — the name is over 200 characters; ask for a shorter one.
- Anything else — relay the status and body verbatim.

Report what happened, not what you expected to happen.
