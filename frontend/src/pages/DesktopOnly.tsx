import { isDesktop } from '../desktop'

/** Library, clips and playtime read the player's own PC, so the web build explains how to get them. */
export function DesktopOnly({ feature, children }: { feature: string; children: React.ReactNode }) {
  if (isDesktop) return <>{children}</>
  return (
    <div className="promo enter">
      <div className="kicker bare">
        <b>//</b> Desktop app
      </div>
      <div className="display" style={{ fontSize: 56 }}>
        {feature}
        <br />
        <span style={{ color: 'var(--volt)' }}>lives on your PC.</span>
      </div>
      <p className="muted" style={{ margin: 0, maxWidth: 560 }}>
        The Clutch desktop app finds every game you have installed, launches them, tracks your playtime, and clips your best moments with a hotkey.
        Run it from the <code>desktop</code> folder with <code>npm start</code>.
      </p>
    </div>
  )
}
