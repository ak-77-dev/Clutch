/**
 * Pointer-driven effects, wired once for the whole app.
 *
 * Surfaces marked in fx.css (cards, report cards, stat rows...) get `--mx` / `--my`, the cursor
 * position inside them, which paints a soft volt spotlight that follows the mouse. One
 * delegated listener, no React re-renders.
 */
const SPOT = '.card, .report, .friend, .stat-row, .goal-form, .onboard-card, .now-card, .game-card'

export function installFx(): void {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  let last: HTMLElement | null = null
  document.addEventListener(
    'pointermove',
    (e) => {
      const el = (e.target as Element | null)?.closest?.(SPOT) as HTMLElement | null
      if (last && last !== el) last.classList.remove('lit')
      last = el
      if (!el) return
      const r = el.getBoundingClientRect()
      el.style.setProperty('--mx', `${e.clientX - r.left}px`)
      el.style.setProperty('--my', `${e.clientY - r.top}px`)
      el.classList.add('lit')
    },
    { passive: true },
  )
  document.addEventListener('pointerleave', () => last?.classList.remove('lit'))
}

/** Tilt handlers for a 3D card: sets --rx / --ry (degrees) and --gx / --gy (glare position). */
export function tiltHandlers(max = 9) {
  const reduce = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (reduce) return {}
  return {
    onPointerMove(e: React.PointerEvent<HTMLElement>) {
      const el = e.currentTarget
      const r = el.getBoundingClientRect()
      const x = (e.clientX - r.left) / r.width
      const y = (e.clientY - r.top) / r.height
      el.style.setProperty('--ry', `${(x - 0.5) * 2 * max}deg`)
      el.style.setProperty('--rx', `${(0.5 - y) * 2 * max}deg`)
      el.style.setProperty('--gx', `${x * 100}%`)
      el.style.setProperty('--gy', `${y * 100}%`)
    },
    onPointerLeave(e: React.PointerEvent<HTMLElement>) {
      const el = e.currentTarget
      el.style.setProperty('--rx', '0deg')
      el.style.setProperty('--ry', '0deg')
    },
  }
}
