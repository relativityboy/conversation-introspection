# Export

Export is the flagship guarantee of the whole project, stated plainly: **what goes in comes back
out identical.** Not "close enough" — byte-for-byte identical.

```bash
uv run introspect export <session-uuid> -o /tmp/exported.jsonl
```

With no `-o`, it writes `<session-uuid>.jsonl` into the current directory. In the TUI it's
`/export <uuid> [path]`.

## Prove it on your own data

Pick any session UUID from your own `~/.claude/projects/<project-slug>/` directory (the filename
minus `.jsonl`), export it, and compare the bytes to the original:

```bash
uv run introspect export <session-uuid> -o /tmp/exported.jsonl
cmp /tmp/exported.jsonl ~/.claude/projects/<project-slug>/<session-uuid>.jsonl
echo $?
```

`cmp` prints nothing and exits `0` when two files are byte-identical. No output — and `echo $?`
printing `0` — means the export is a perfect reconstruction of the original. That's the whole point,
verified on your own data.

## Why it's exact

Capture stores each transcript line's *raw bytes* in the archive's `raw_records` layer, in order.
Export is simply the inverse: concatenate a transcript's stored raw bytes back in line order. Every
byte that was on disk is in the archive — including the trailing newline, or its deliberate absence
on a torn final line — so concatenation reproduces the source file with no re-serialization
anywhere in the loop. And it works **even if the original file is already gone**; the archive is the
system of record, not a cache of the file.

Large transcripts stream straight to disk, one line at a time, so a session with multi-megabyte
lines exports without ever being fully held in memory.

## Which copy you get back

A single transcript can have more than one source file on disk over its life (a main file plus a
`.bak`; an older diverged copy plus the live one). Export always reconstructs the **current**
transcript:

- Normally, its **primary** (live) source file — always the whole current file.
- If no source file is primary (for example a transcript we only ever saw as a backup), export falls
  back to the **most-complete** copy available, so you still get the best full reconstruction.

Older diverged generations aren't reachable through export by design — export is always about the
live file. (Those historical bytes remain in the archive for forensic queries; surfacing them is a
separate concern.)

## The guarantee is enforced by a test

This isn't a promise on trust. The project's flagship test imports a transcript tree, exports it
back out, and byte-compares the result to the source. It's the bar every change to the codebase has
to clear: if a change can't survive the round trip, it isn't done. See
[the dev guide](../dev/README.md#the-flagship-test-import--export-round-trip).

## A privacy note

An export is a plaintext copy of a real conversation, verbatim — including anything you ever pasted
into a prompt. Treat exported `.jsonl` files with the same care as the archive itself: don't paste
them into a chat, an issue, or a support ticket without reviewing exactly what's in them. See
[How the archive protects you](how-the-archive-protects-you.md#local-only-and-whats-actually-in-the-file).
