// The honest marker for a thinking block. The CLI does not persist thinking content to the
// archive, so there is nothing to show and nothing to expand — the glyph stands in for the fact
// that reasoning happened here. It renders regardless of whether text_content is empty (it
// almost always is); the point is to be truthful about the gap, not to imply hidden content.
const THINKING_LABEL = 'thinking occurred — content not persisted by the CLI'

export function ThinkingGlyph() {
  return (
    <div
      className="thinking-glyph mono"
      role="img"
      aria-label={THINKING_LABEL}
      title={THINKING_LABEL}
      style={{
        fontFamily: 'var(--mono)',
        fontSize: 14,
        lineHeight: 1,
        color: 'var(--dragonfly)',
        opacity: 0.55,
        margin: '6px 0',
      }}
    >
      ◌
    </div>
  )
}
