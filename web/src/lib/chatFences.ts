// NOTE(claude): view-layer-only normalizer for "chat-style" code fences typed by a human in the
// reading room (Task 1, 2026-09-14 chat-fence-normalizer plan). The owner types ``` the way a chat
// UI's autocomplete/paste behavior encourages — opened mid-line, or closed at the end of a content
// line instead of alone on its own line — none of which CommonMark recognizes as a real fence. Left
// alone, that content either collapses into one garbled multi-line INLINE code span (newlines
// eaten) or swallows every paragraph after it (an opener that never finds its closer). This
// function reshapes ONLY the presentation the reading room renders; it is applied at render time to
// human-authored text blocks (see FENCE_NORMALIZED_KINDS in MessageTurn.tsx) and never touches the
// archive — the stored record bytes are exactly what was captured, and the raw record inspector
// always shows them unmodified. Claude's own prose is already correct CommonMark (fences on their
// own lines) and MUST NEVER be passed through this function: doing so would risk rewriting
// Claude-authored code blocks that only coincidentally look "chat-style" (e.g. a nested fence
// example inside a real one).
export function normalizeChatFences(text: string): string {
  const lines = text.split('\n')
  const out: string[] = []
  let state: 'OUTSIDE' | 'INSIDE' = 'OUTSIDE'

  // A run is a maximal sequence of 3+ backticks. Reused verbatim on emit so four-backtick (or
  // longer) runs are preserved rather than collapsed to three.
  const RUN = /`{3,}/
  const RUN_G = /`{3,}/g

  function countRuns(line: string): number {
    const matches = line.match(RUN_G)
    return matches ? matches.length : 0
  }

  // Opener line, already split into what precedes the run, the run itself, and what follows it.
  // `prefix` is emitted verbatim (never touched) ahead of whichever of the two opener shapes
  // applies — the brief's rule 3 keeps a blank (≤3-space) prefix in place rather than stripping it.
  function emitOpener(prefix: string, run: string, rest: string): void {
    const trimmedRest = rest.trim()
    if (trimmedRest === '' || ALLOWLIST.has(trimmedRest.toLowerCase())) {
      // Already a correct CommonMark opener (rest is a real info string) — emit unchanged.
      out.push(prefix + run + rest)
    } else {
      out.push(prefix + run)
      out.push(rest.trimStart())
    }
    state = 'INSIDE'
  }

  function processOutsideLine(line: string): void {
    const runCount = countRuns(line)
    if (runCount === 0 || runCount >= 2) {
      // Rules 1 & 2: nothing to do — no fence marker, or an inline code span like `` ```x``` ``.
      out.push(line)
      return
    }

    // Rule 3: exactly one run.
    const match = line.match(RUN) as RegExpMatchArray
    const idx = match.index as number
    const run = match[0]
    const prefix = line.slice(0, idx)
    const rest = line.slice(idx + run.length)

    if (prefix.trim() !== '') {
      // Mid-line opener: split the leading prose onto its own line, then treat the run+rest as a
      // fresh opener line with an empty (blank) prefix.
      out.push(prefix.trimEnd())
      emitOpener('', run, rest)
    } else {
      emitOpener(prefix, run, rest)
    }
  }

  function processInsideLine(line: string): void {
    const trimmed = line.trim()
    if (/^`{3,}$/.test(trimmed)) {
      // Rule 4: a real closer, alone on its line — emit as is.
      out.push(line)
      state = 'OUTSIDE'
      return
    }

    if (countRuns(line) === 1) {
      const match = line.match(RUN) as RegExpMatchArray
      const idx = match.index as number
      const run = match[0]
      const before = line.slice(0, idx)
      const after = line.slice(idx + run.length)
      const beforeBlank = before.trim() === ''
      const afterBlank = after.trim() === ''

      if (!beforeBlank && afterBlank) {
        // Rule 5: closer glued to the end of the last content line.
        out.push(before.trimEnd())
        out.push(run)
        state = 'OUTSIDE'
        return
      }

      if (!beforeBlank && !afterBlank) {
        // Rule 6 (shape d): close-and-reopen on one line — the trailing text may itself be a fresh
        // mid-line opener, so it re-enters the OUTSIDE machinery rather than being emitted as is.
        out.push(before.trimEnd())
        out.push(run)
        state = 'OUTSIDE'
        processOutsideLine(after)
        return
      }

      // beforeBlank && !afterBlank (a run at the very start of the line, content after it): this is
      // fence CONTENT — e.g. a markdown example nested inside an already-open, correctly-formed
      // fence — never a real delimiter. Falls through to rule 7, unchanged, state untouched.
    }

    // Rule 7: everything else (including 2+ runs on one line) is fence content — emit as is.
    out.push(line)
  }

  for (const line of lines) {
    if (state === 'OUTSIDE') processOutsideLine(line)
    else processInsideLine(line)
  }

  // End of input: if still INSIDE, the opener never closed. No closer is invented — the fence
  // simply runs to the end of the block, same as CommonMark's own behavior for an unclosed fence.
  return out.join('\n')
}

// Case-insensitive single-token info strings that read as a real CommonMark opener rather than
// lost prose. Matches the brief verbatim.
const ALLOWLIST = new Set([
  'bash',
  'sh',
  'zsh',
  'shell',
  'console',
  'powershell',
  'ps1',
  'python',
  'py',
  'js',
  'javascript',
  'ts',
  'typescript',
  'tsx',
  'jsx',
  'json',
  'jsonl',
  'yaml',
  'yml',
  'toml',
  'ini',
  'xml',
  'html',
  'css',
  'scss',
  'md',
  'markdown',
  'text',
  'txt',
  'diff',
  'patch',
  'go',
  'rust',
  'rs',
  'c',
  'cpp',
  'c++',
  'cs',
  'java',
  'kotlin',
  'swift',
  'ruby',
  'rb',
  'php',
  'sql',
  'graphql',
  'http',
  'mermaid',
  'dockerfile',
  'makefile',
  'make',
  'lua',
  'r',
  'perl',
  'elixir',
  'haskell',
  'hs',
  'scala',
  'clojure',
  'objectivec',
  'objc',
  'dart',
  'zig',
  'nim',
  'plaintext',
  'none',
])
