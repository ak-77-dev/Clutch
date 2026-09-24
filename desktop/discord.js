// Discord Rich Presence over Discord's local IPC pipe (no dependency).
// Protocol: frames of [op: int32 LE][length: int32 LE][JSON]; op 0 = handshake, 1 = frame, 2 = close.
'use strict'
const net = require('node:net')
const crypto = require('node:crypto')

function encode(op, payload) {
  const json = Buffer.from(JSON.stringify(payload), 'utf8')
  const header = Buffer.alloc(8)
  header.writeInt32LE(op, 0)
  header.writeInt32LE(json.length, 4)
  return Buffer.concat([header, json])
}

/** Split a byte stream into complete frames; returns [frames, leftover]. */
function decode(buffer) {
  const frames = []
  let offset = 0
  while (buffer.length - offset >= 8) {
    const op = buffer.readInt32LE(offset)
    const len = buffer.readInt32LE(offset + 4)
    if (buffer.length - offset - 8 < len) break
    const body = buffer.subarray(offset + 8, offset + 8 + len).toString('utf8')
    let data = null
    try {
      data = JSON.parse(body)
    } catch {
      /* ignore */
    }
    frames.push({ op, data })
    offset += 8 + len
  }
  return [frames, buffer.subarray(offset)]
}

function pipePath(i) {
  return process.platform === 'win32' ? `\\\\?\\pipe\\discord-ipc-${i}` : `${process.env.XDG_RUNTIME_DIR || process.env.TMPDIR || '/tmp'}/discord-ipc-${i}`
}

class DiscordPresence {
  constructor(clientId) {
    this.clientId = clientId
    this.socket = null
    this.ready = false
    this.lastError = null
    this.pending = null
  }

  /** Connect and handshake; resolves true when Discord accepted the client id. */
  connect(timeoutMs = 3000) {
    return new Promise((resolve) => {
      let i = 0
      const tryNext = () => {
        if (i > 9) {
          this.lastError = 'Discord isn’t running'
          return resolve(false)
        }
        const socket = net.createConnection(pipePath(i++))
        let settled = false
        let rest = Buffer.alloc(0)
        const done = (ok, err) => {
          if (settled) return
          settled = true
          if (!ok) {
            this.lastError = err
            socket.destroy()
          }
          resolve(ok)
        }
        const timer = setTimeout(() => done(false, 'Discord didn’t answer'), timeoutMs)
        socket.once('error', () => {
          clearTimeout(timer)
          if (!settled) {
            settled = true
            tryNext()
          }
        })
        socket.on('connect', () => socket.write(encode(0, { v: 1, client_id: this.clientId })))
        socket.on('data', (chunk) => {
          const [frames, left] = decode(Buffer.concat([rest, chunk]))
          rest = left
          for (const f of frames) {
            if (f.op === 1 && f.data?.evt === 'READY') {
              clearTimeout(timer)
              this.socket = socket
              this.ready = true
              socket.on('close', () => {
                this.ready = false
                this.socket = null
              })
              done(true)
            } else if (f.op === 2 || f.data?.evt === 'ERROR') {
              clearTimeout(timer)
              done(false, f.data?.message || f.data?.data?.message || 'Discord refused the connection')
            }
          }
        })
      }
      tryNext()
    })
  }

  setActivity(activity) {
    if (!this.ready || !this.socket) return false
    this.socket.write(encode(1, { cmd: 'SET_ACTIVITY', args: { pid: process.pid, activity: activity || null }, nonce: crypto.randomUUID() }))
    return true
  }

  clear() {
    return this.setActivity(null)
  }

  close() {
    if (this.socket) this.socket.end()
    this.socket = null
    this.ready = false
  }
}

/** What Discord shows while playing: "Playing <app>", then these two lines. */
function activityFor(session, startedAt) {
  if (!session?.playing) return null
  const bits = []
  if (session.rank) bits.push(session.rank)
  if (session.today && session.today.wins + session.today.losses) bits.push(`${session.today.wins}W ${session.today.losses}L today`)
  if (session.session_clips) bits.push(`${session.session_clips} clip${session.session_clips > 1 ? 's' : ''}`)
  return {
    details: `Playing ${session.playing.game_name}`,
    state: bits.join(' · ') || 'Clutch is recording highlights',
    timestamps: { start: Math.round((startedAt || session.playing.started_at) * 1000) },
    instance: false,
  }
}

module.exports = { DiscordPresence, encode, decode, activityFor }
