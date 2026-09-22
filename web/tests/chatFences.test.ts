import { fromMarkdown } from 'mdast-util-from-markdown'
import type { Code, Paragraph, RootContent } from 'mdast'
import { describe, expect, it } from 'vitest'
import { normalizeChatFences } from '../src/lib/chatFences'

// Minimal text extraction for a one-line paragraph (its single `text` child) — avoids pulling in
// mdast-util-to-string, which the brief doesn't sanction as a test dependency.
function paragraphText(node: RootContent): string {
  const paragraph = node as Paragraph
  const child = paragraph.children[0]
  return child.type === 'text' ? child.value : ''
}

describe('normalizeChatFences', () => {
  // Shape (a): opening ``` mid-line after prose, closed mid-line three lines later — CommonMark
  // reads the whole span as one multi-line INLINE code span (newlines collapse). Exact string from
  // the brief's Problem section.
  it('splits a mid-line opener (shape a) onto its own fence, keeping the leading prose and the trailing prose', () => {
    const input = 'Console outputs ```[jetwalls] site\n  more\n  functions.``` I have…'
    const expected =
      'Console outputs\n```\n[jetwalls] site\n  more\n  functions.\n```\n I have…'
    expect(normalizeChatFences(input)).toBe(expected)
  })

  // Shape (b): opener on its own line but immediately followed by content — CommonMark treats the
  // content as the info string and it is lost entirely.
  it('splits an opener immediately followed by content (shape b) so the content survives as a fence line', () => {
    const input = '```Batteries: 3x LiPo — $200'
    const expected = '```\nBatteries: 3x LiPo — $200'
    expect(normalizeChatFences(input)).toBe(expected)
  })

  // Shape (c): closing ``` glued to the end of the last content line instead of its own line — the
  // block never closes and swallows every following paragraph.
  it('splits a closer glued to the end of a content line (shape c) onto its own line', () => {
    const input = '```\nRC: ELRS — $150```'
    const expected = '```\nRC: ELRS — $150\n```'
    expect(normalizeChatFences(input)).toBe(expected)
  })

  // Shape (d): close-and-reopen on one line. The brief's own example here is a markdown-escaping
  // artifact (a single backtick used to quote text that itself contains backticks), so the exact
  // literal string isn't recoverable byte-for-byte from the prose. Reconstructed from the brief's
  // two adjacent fragments — `  },` (content before) and `"type": "user",` (content after) glued
  // by the run, matching a real pasted-JSON-transcript line — to exercise rule 6 exactly: content
  // before AND after a single run, the run closes the open fence, and the trailing text is
  // re-processed as a fresh OUTSIDE line.
  it('closes and re-processes the trailing text on one line (shape d)', () => {
    const input = '```\n  },```"type": "user",'
    const expected = '```\n  },\n```\n"type": "user",'
    expect(normalizeChatFences(input)).toBe(expected)
  })

  it('returns correct CommonMark input byte-identical (own-line opener with info string, own-line closer, prose after)', () => {
    const input = "Some prose.\n```python\nprint('hi')\n```\nMore prose."
    expect(normalizeChatFences(input)).toBe(input)
  })

  it('leaves an inline ```x``` code span on one line untouched (two-or-more runs, rule 2)', () => {
    const input = 'before ```x``` after'
    expect(normalizeChatFences(input)).toBe(input)
  })

  it('leaves a ``` that starts a content line inside an already-open correct fence untouched (e.g. a markdown example nested in a real fence)', () => {
    const input = '```\nExample of a fence:\n```js\nmore stuff\n```'
    expect(normalizeChatFences(input)).toBe(input)
  })

  it('preserves four-backtick runs rather than collapsing them to three', () => {
    const input = '````\ncode here\n````'
    expect(normalizeChatFences(input)).toBe(input)
  })

  it('turns an opener with an unknown single token into the first content line', () => {
    const input = '```Session\nsome content'
    const expected = '```\nSession\nsome content'
    expect(normalizeChatFences(input)).toBe(expected)
  })

  it('leaves an unclosed opener unclosed — never invents a closer', () => {
    const input = '```Notes here\nmore stuff'
    const expected = '```\nNotes here\nmore stuff'
    expect(normalizeChatFences(input)).toBe(expected)
  })

  const allInputs = [
    'Console outputs ```[jetwalls] site\n  more\n  functions.``` I have…',
    '```Batteries: 3x LiPo — $200',
    '```\nRC: ELRS — $150```',
    '```\n  },```"type": "user",',
    "Some prose.\n```python\nprint('hi')\n```\nMore prose.",
    'before ```x``` after',
    '```\nExample of a fence:\n```js\nmore stuff\n```',
    '````\ncode here\n````',
    '```Session\nsome content',
    '```Notes here\nmore stuff',
    "Here's the build:\n```Batteries: 3x LiPo — $200\nRC: ELRS — $150```\nThanks for reviewing.",
    'no fences at all, just prose',
    '',
  ]

  it.each(allInputs)('is idempotent: normalizeChatFences(normalizeChatFences(x)) === normalizeChatFences(x)', (input) => {
    const once = normalizeChatFences(input)
    expect(normalizeChatFences(once)).toBe(once)
  })

  // The diagnosis in the brief: (b)'s content is normally lost as an info string, and (c) usually
  // follows it, swallowing everything after. Verify the FIXED shape by parsing the normalized
  // output as real CommonMark (mdast-util-from-markdown, already a react-markdown dependency) and
  // checking the node sequence rather than just the raw string.
  it('parses to paragraph, code (with both fence lines as its value), paragraph — (b) no longer loses its first line and (c) no longer swallows the trailing paragraph', () => {
    const input =
      "Here's the build:\n```Batteries: 3x LiPo — $200\nRC: ELRS — $150```\nThanks for reviewing."
    const normalized = normalizeChatFences(input)

    const tree = fromMarkdown(normalized)
    expect(tree.children.map((node) => node.type)).toEqual(['paragraph', 'code', 'paragraph'])

    const [firstParagraph, codeNode, lastParagraph] = tree.children
    expect(paragraphText(firstParagraph)).toBe("Here's the build:")
    expect((codeNode as Code).value).toBe('Batteries: 3x LiPo — $200\nRC: ELRS — $150')
    expect(paragraphText(lastParagraph)).toBe('Thanks for reviewing.')
  })
})
