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

const { encode, decode, activityFor } = require('../discord')
const { eventNotification } = require('../lib')

test('discord IPC frames round-trip, including split reads', () => {
  const a = encode(1, { cmd: 'SET_ACTIVITY', nonce: 'x' })
  const b = encode(2, { message: 'bye' })
  const both = Buffer.concat([a, b])
  const [first, rest] = decode(both.subarray(0, a.length + 5))
  assert.deepEqual(first, [{ op: 1, data: { cmd: 'SET_ACTIVITY', nonce: 'x' } }])
  const [second, none] = decode(Buffer.concat([rest, both.subarray(a.length + 5)]))
  assert.deepEqual(second, [{ op: 2, data: { message: 'bye' } }])
  assert.equal(none.length, 0)
})

test('rich presence text', () => {
  assert.equal(activityFor({ playing: null }), null)
  const act = activityFor({ playing: { game_name: 'VALORANT', started_at: 100 }, rank: 'Gold 3', today: { wins: 3, losses: 1 }, session_clips: 2 })
  assert.equal(act.details, 'Playing VALORANT')
  assert.equal(act.state, 'Gold 3 · 3W 1L today · 2 clips')
  assert.equal(act.timestamps.start, 100000)
})

test('highlight, goal and report notifications', () => {
  assert.deepEqual(overlayFor({ type: 'clip_saved', clip: { kind: 'clip', title: '⚡ Triple kill · Dota 2' } }).title, 'Highlight clipped')
  assert.equal(overlayFor({ type: 'clip_saved', clip: { kind: 'export', title: 'x' } }), null)
  assert.equal(overlayFor({ type: 'goal', state: 'over', goal: { target: 3, progress: { value: 3.2 } } }).tone, 'error')
  const r = eventNotification({ type: 'report_ready', report: { game_name: 'Dota 2', seconds: 5400, clips: [1, 2], stats: { wins: 3, losses: 2 } } })
  assert.equal(r.title, 'Dota 2 report card · 3W 2L')
  assert.equal(eventNotification({ type: 'goal', state: 'done', goal: { kind: 'rank', label: 'Diamond 1' } }).body, 'Reached Diamond 1')
  assert.equal(eventNotification({ type: 'buffer' }), null)
})
