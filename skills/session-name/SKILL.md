---
name: session-name
description: Names the CURRENT Claude session in the conversation-introspection archive (the title shown in its TUI and reading room). User-invoked only — `/session-name <name>`.
argument-hint: <name>
disable-model-invocation: true
---

```!
cd __INTROSPECT_SERVER_DIR__ && uv run introspect session-name --stdin <<'INTROSPECT_SESSION_NAME'
$ARGUMENTS
INTROSPECT_SESSION_NAME
```

The line above is the result — the command already ran, before you saw this. Relay it to
the user verbatim, in one sentence, and do nothing else. If it says the server isn't
running, do not start it.
