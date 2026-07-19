// NOTE(claude): v1 shows only a marker chip — no decode. The archive stores the image payload
// (base64) so nothing is lost; rendering it in the reading room is deferred (YAGNI). The room
// needn't show the image yet, only acknowledge that one was present in the turn.
export function ImageBlock() {
  return (
    <span
      className="image-chip mono"
      style={{
        display: 'inline-block',
        fontFamily: 'var(--mono)',
        fontSize: 11,
        color: 'var(--mist)',
        border: '1px solid var(--shore)',
        borderRadius: 4,
        padding: '1px 6px',
        margin: '6px 0',
      }}
    >
      [image]
    </span>
  )
}
