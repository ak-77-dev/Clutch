import { useEffect, useRef, useState } from 'react'

const reduce = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

/** A number that counts up to its value (ease-out, ~0.9 s), and eases between later changes. */
export function CountUp({ value, decimals = 0, duration = 900 }: { value: number; decimals?: number; duration?: number }) {
  const [shown, setShown] = useState(reduce ? value : 0)
  const from = useRef(reduce ? value : 0)
  useEffect(() => {
    if (reduce) {
      setShown(value)
      return
    }
    const start = performance.now()
    const a = from.current
    let raf = 0
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / duration)
      const v = a + (value - a) * (1 - Math.pow(1 - t, 3))
      setShown(v)
      from.current = v
      if (t < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [value, duration])
  return <>{shown.toFixed(decimals)}</>
}
