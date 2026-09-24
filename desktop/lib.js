// Pure helpers for the Electron main process (no Electron imports, so `node --test` covers them).
'use strict'
const fs = require('node:fs')
const net = require('node:net')
const path = require('node:path')

/** The Python that runs the Clutch backend: $CLUTCH_PYTHON, the backend's venv, or python on PATH. */
function findPython(backendDir, env = process.env, exists = fs.existsSync) {
  if (env.CLUTCH_PYTHON) return env.CLUTCH_PYTHON
  const candidates = [path.join(backendDir, '.venv', 'Scripts', 'python.exe'), path.join(backendDir, '.venv', 'bin', 'python')]
  return candidates.find((p) => exists(p)) ?? 'python'
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.unref()
    srv.on('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address()
      srv.close(() => resolve(port))
    })
  })
}

/** Split a Server-Sent Events buffer into complete events and the unfinished remainder. */
function parseSSE(buffer) {
  const events = []
  const blocks = buffer.split(/\r?\n\r?\n/)
  const rest = blocks.pop()
  for (const block of blocks) {
    let type = 'message'
    const data = []
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith('event:')) type = line.slice(6).trim()
      else if (line.startsWith('data:')) data.push(line.slice(5).trim())
    }
    if (!data.length) continue
    try {
      events.push({ type, ...JSON.parse(data.join('\n')) })
    } catch {
      /* ignore malformed frames */
    }
  }
  return { events, rest }
}

function hours(seconds) {
  const s = Math.round(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h ? `${h}h ${m}m` : `${m}m`
}

/** What the in-game overlay says for an event (null = stay quiet). */
function overlayFor(event) {
  switch (event.type) {
    case 'clip_saved': {
      const c = event.clip || {}
      if ((c.title || '').startsWith('⚡')) return { title: 'Highlight clipped', sub: c.title.slice(1).trim(), tone: 'ok', sound: 'clip' }
      const what = c.kind === 'screenshot' ? 'Screenshot saved' : c.kind === 'recording' ? 'Recording saved' : c.kind === 'export' ? null : 'Clip saved'
      if (!what) return null // exports are made from inside the app
      // The sound already played when the key was pressed (see pressFeedback); this just confirms.
      return { title: what, sub: [c.game_name || 'Desktop', c.duration ? `${Math.round(c.duration)}s` : null].filter(Boolean).join(' · '), tone: 'ok', sound: null }
    }
    case 'recording':
      return event.recording ? { title: 'Recording', sub: 'Press again to stop', tone: 'rec', sound: 'start' } : null
    case 'game_started':
      return { title: event.game_name, sub: 'Clutch is tracking · replay buffer armed', tone: 'info', sound: null }
    case 'error':
      return { title: 'Clutch', sub: event.message, tone: 'error', sound: 'error' }
    case 'goal': {
      const g = event.goal || {}
      if (event.state === 'over') return { title: 'Daily cap reached', sub: `${g.progress?.value}h played today — your limit is ${g.target}h`, tone: 'error', sound: 'error' }
      if (event.state === 'close') return { title: 'Almost at your cap', sub: `${g.progress?.value}h of ${g.target}h today`, tone: 'info', sound: null }
      return null
    }
    default:
      return null
  }
}

/** Instant feedback the moment a hotkey is pressed, before the backend has done anything. */
function pressFeedback(kind, settings = {}, recording = false) {
  if (kind === 'clip') return { title: 'Clipping', sub: `Saving the last ${settings.buffer_seconds || 60}s…`, tone: 'ok', sound: 'clip' }
  if (kind === 'record') return recording ? { title: 'Saving recording', sub: 'Stitching it together…', tone: 'ok', sound: 'clip' } : null
  if (kind === 'screenshot') return { title: 'Screenshot', sub: 'Saved to your clips', tone: 'ok', sound: 'clip' }
  return null
}

/** Desktop notification for a finished session (the overlay is gone by then). */
function sessionNotification(event) {
  if (event.type !== 'session_end' || event.notify === false) return null
  const clips = event.clips ? ` · ${event.clips} clip${event.clips > 1 ? 's' : ''} saved` : ''
  return { title: `${event.game_name} · ${hours(event.seconds)}`, body: `Session recap${clips}. Open Clutch to review.` }
}

/** Desktop notifications for things that happen after the game (report cards, goals, cleanups). */
function eventNotification(event) {
  if (event.type === 'report_ready') {
    const r = event.report || {}
    const s = r.stats
    const record = s ? ` · ${s.wins}W ${s.losses}L` : ''
    return { title: `${r.game_name} report card${record}`, body: `${hours(r.seconds || 0)} played${r.clips?.length ? ` · ${r.clips.length} clips` : ''}. Open Clutch for the details.` }
  }
  if (event.type === 'goal' && event.state === 'done') return { title: 'Goal reached', body: goalName(event.goal) }
  if (event.type === 'storage_cleaned') return { title: 'Clips folder tidied', body: `${event.count} old clip${event.count > 1 ? 's' : ''} moved to the Recycle Bin.` }
  return null
}

function goalName(g = {}) {
  return (
    {
      weekly_hours: `Played ${g.target}h this week`,
      clips: `${g.target} clips this week`,
      win_rate: `${g.target}% win rate`,
      rank: `Reached ${g.label || 'your rank goal'}`,
    }[g.kind] || 'Goal complete'
  )
}

module.exports = { findPython, freePort, parseSSE, overlayFor, pressFeedback, sessionNotification, eventNotification, hours }
