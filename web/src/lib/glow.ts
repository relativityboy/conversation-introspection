/**
 * Deep-link arrival glow: when the reader opens at a specific message
 * (`/s/{uuid}/m/{msgUuid}`), the target row briefly washes dawn-gold, then fades.
 *
 * The glow is a CSS animation (`.deep-link-glow` in theme.css). This module's job is only to
 * add the class, then remove it once — on the animation's own `animationend`, with a timeout
 * fallback because jsdom (and any environment that doesn't actually run CSS animations) never
 * fires `animationend`, so the class would otherwise stick forever.
 *
 * `prefers-reduced-motion` is honored strictly: under reduce we SCROLL the target into view but
 * NEVER add the class, so no animation runs at all.
 */

const GLOW_CLASS = 'deep-link-glow'

// Slightly longer than the 2s CSS fade so a real browser's `animationend` wins the race and the
// timeout is only the safety net where no animation runs (jsdom, reduced-motion-at-the-OS, etc.).
const FALLBACK_MS = 2500

function prefersReducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

export function applyGlow(el: HTMLElement): void {
  // Center the target regardless of motion preference — the scroll is the "you arrived here"
  // signal that must survive even when the glow itself is suppressed. Guarded because jsdom
  // historically ships scrollIntoView as a missing/no-op member.
  if (typeof el.scrollIntoView === 'function') {
    el.scrollIntoView({ block: 'center' })
  }

  if (prefersReducedMotion()) return

  el.classList.add(GLOW_CLASS)

  // Idempotent removal (classList.remove is a no-op if absent), reached by whichever comes first:
  // the animation's own end, or the fallback timeout for environments that never run it.
  const remove = () => el.classList.remove(GLOW_CLASS)
  const fallback = setTimeout(remove, FALLBACK_MS)
  el.addEventListener(
    'animationend',
    () => {
      clearTimeout(fallback)
      remove()
    },
    { once: true },
  )
}
