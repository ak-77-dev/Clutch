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
