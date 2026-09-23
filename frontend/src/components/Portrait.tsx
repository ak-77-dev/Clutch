import { useState } from 'react'

/** Character / profile image with an initials fallback (missing art, offline CDN, RL cars). */
export function Portrait({ src, name, size = '' }: { src: string | null | undefined; name: string; size?: '' | 'sm' | 'xs' }) {
  const [failed, setFailed] = useState(false)
  const cls = `portrait ${size}`.trim()
  if (!src || failed) {
    const initials = name.replace(/[^A-Za-z0-9 ]/g, '').split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase() || '?'
    return (
      <div className={cls} aria-label={name} role="img">
        {initials}
      </div>
    )
  }
  return <img className={cls} src={src} alt={name} loading="lazy" onError={() => setFailed(true)} />
}
