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
      const what = c.kind === 'screenshot' ? 'Screenshot saved' : c.kind === 'recording' ? 'Recording saved' : 'Clip saved'
      return { title: what, sub: [c.game_name || 'Desktop', c.duration ? `${Math.round(c.duration)}s` : null].filter(Boolean).join(' · '), tone: 'ok', sound: 'clip' }
    }
    case 'recording':
      return event.recording ? { title: 'Recording', sub: 'Press again to stop', tone: 'rec', sound: 'start' } : null
    case 'game_started':
      return { title: event.game_name, sub: 'Clutch is tracking · replay buffer armed', tone: 'info', sound: null }
    case 'error':
      return { title: 'Clutch', sub: event.message, tone: 'error', sound: 'error' }
    default:
      return null
  }
}

/** Desktop notification for a finished session (the overlay is gone by then). */
function sessionNotification(event) {
  if (event.type !== 'session_end' || event.notify === false) return null
  const clips = event.clips ? ` · ${event.clips} clip${event.clips > 1 ? 's' : ''} saved` : ''
  return { title: `${event.game_name} · ${hours(event.seconds)}`, body: `Session recap${clips}. Open Clutch to review.` }
}

module.exports = { findPython, freePort, parseSSE, overlayFor, sessionNotification, hours }
