import { useRef, useState } from 'react'
import { desktop, type Clip } from '../desktop'
import { fmtBytes, fmtClock, timeAgoUnix } from '../format'
import { StarIcon } from './Icons'

const KIND: Record<Clip['kind'], string> = { clip: 'Clip', recording: 'Rec', screenshot: 'Shot', export: 'Export' }

/** A clip card: thumbnail, and on hover a muted preview that scrubs with the mouse (like Medal). */
/** `order` (1-based) marks the tile as picked in select mode, e.g. for a montage. */
export function ClipTile({ clip, onOpen, order }: { clip: Clip; onOpen: () => void; order?: number | null }) {
  const video = useRef<HTMLVideoElement>(null)
  const [previewing, setPreviewing] = useState(false)
  const [pos, setPos] = useState<number | null>(null)
  const isVideo = clip.kind !== 'screenshot' && !clip.path.toLowerCase().endsWith('.gif')

  function onMove(e: React.MouseEvent<HTMLDivElement>) {
    if (!isVideo || !clip.duration || !video.current) return
    const r = e.currentTarget.getBoundingClientRect()
    const f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width))
    setPos(f)
    if (video.current.readyState >= 1) video.current.currentTime = f * clip.duration
  }

  return (
    <button className={`ctile brackets ${order ? 'picked' : ''}`} onClick={onOpen} title={clip.title} aria-pressed={order === undefined ? undefined : Boolean(order)}>
      <div
        className={`thumb ${previewing ? 'previewing' : ''}`}
        onMouseEnter={() => isVideo && setPreviewing(true)}
        onMouseLeave={() => {
          setPreviewing(false)
          setPos(null)
        }}
        onMouseMove={onMove}
      >
        {clip.thumb ? <img src={desktop.clipThumb(clip.id)} alt="" loading="lazy" /> : <div className="skeleton" style={{ position: 'absolute', inset: 0 }} />}
        {previewing && <video ref={video} src={desktop.clipFile(clip.id)} muted playsInline preload="metadata" onLoadedMetadata={(e) => void e.currentTarget.play().catch(() => {})} loop />}
        <span className="badge kind">{KIND[clip.kind]}</span>
        {clip.favorite && (
          <span className="fav">
            <StarIcon filled />
          </span>
        )}
        {clip.duration ? <span className="dur">{fmtClock(clip.duration)}</span> : null}
        {pos !== null && <span className="scrub" style={{ width: `${pos * 100}%` }} />}
        {order ? <span className="pick">{order}</span> : null}
        {clip.share_url ? <span className="badge shared">Shared</span> : null}
      </div>
      <div className="ctitle">{clip.title}</div>
      <div className="cmeta">
        <span>{timeAgoUnix(clip.created_at)}</span>
        <span>{fmtBytes(clip.size)}</span>
        {clip.height ? <span>{clip.height}p</span> : null}
      </div>
    </button>
  )
}
