import { useEffect, useState } from 'react'
import { accelerator } from '../format'

/** Click, then press a key combination (Esc cancels). */
export function Hotkey({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [listening, setListening] = useState(false)
  useEffect(() => {
    if (!listening) return
    function onKey(e: KeyboardEvent) {
      e.preventDefault()
      if (e.key === 'Escape') return setListening(false)
      const acc = accelerator(e)
      if (acc) {
        onChange(acc)
        setListening(false)
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [listening, onChange])
  return (
    <button className={`hotkey ${listening ? 'listening' : ''}`} onClick={() => setListening(true)} onBlur={() => setListening(false)}>
      {listening ? 'Press a key…' : value.replace('CommandOrControl', 'Ctrl')}
    </button>
  )
}

export function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return <button className="switch" role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)} />
}
