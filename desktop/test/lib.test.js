'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { findPython, freePort, parseSSE, overlayFor, pressFeedback, sessionNotification, hours } = require('../lib')

test('findPython prefers the env override, then the backend venv', () => {
  assert.equal(findPython('/b', { CLUTCH_PYTHON: '/custom/python' }), '/custom/python')
  const venv = path.join('/b', '.venv', 'Scripts', 'python.exe')
  assert.equal(findPython('/b', {}, (p) => p === venv), venv)
  assert.equal(findPython('/b', {}, () => false), 'python')
})

test('freePort returns a usable port', async () => {
  const port = await freePort()
  assert.ok(port > 0 && port < 65536)
})

test('parseSSE splits complete events and keeps the remainder', () => {
  const { events, rest } = parseSSE('retry: 2000\n\nevent: clip_saved\ndata: {"clip":{"kind":"clip"}}\n\n: keep-alive\n\nevent: buffer\ndata: {"active"')
  assert.deepEqual(events, [{ type: 'clip_saved', clip: { kind: 'clip' } }])
  assert.equal(rest, 'event: buffer\ndata: {"active"')
})

test('overlay messages', () => {
  assert.deepEqual(overlayFor({ type: 'clip_saved', clip: { kind: 'clip', game_name: 'VALORANT', duration: 60.2 } }), {
    title: 'Clip saved',
    sub: 'VALORANT · 60s',
    tone: 'ok',
    sound: null, // the press already beeped
  })
  assert.equal(pressFeedback('clip', { buffer_seconds: 90 }).sub, 'Saving the last 90s…')
  assert.equal(pressFeedback('clip').sound, 'clip')
  assert.equal(pressFeedback('record', {}, false), null) // starting a recording announces itself via the event
  assert.equal(overlayFor({ type: 'clip_saved', clip: { kind: 'screenshot' } }).sub, 'Desktop')
  assert.equal(overlayFor({ type: 'recording', recording: true }).tone, 'rec')
  assert.equal(overlayFor({ type: 'recording', recording: false }), null)
  assert.equal(overlayFor({ type: 'buffer' }), null)
})

test('session recap notifications respect the setting', () => {
  const e = { type: 'session_end', game_name: 'Dota 2', seconds: 5400, clips: 2 }
  assert.deepEqual(sessionNotification(e), { title: 'Dota 2 · 1h 30m', body: 'Session recap · 2 clips saved. Open Clutch to review.' })
  assert.equal(sessionNotification({ ...e, notify: false }), null)
  assert.equal(hours(59 * 60), '59m')
})
