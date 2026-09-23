// Clutch desktop shell: runs the Python backend, hosts the UI, owns the tray,
// global hotkeys and the in-game overlay.
'use strict'
const { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain, dialog, screen, Notification, shell, nativeImage } = require('electron')
const { spawn } = require('node:child_process')
const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { findPython, freePort, parseSSE, overlayFor, pressFeedback, sessionNotification } = require('./lib')

const ROOT = path.resolve(__dirname, '..')
const BACKEND_DIR = process.env.CLUTCH_BACKEND_DIR || path.join(ROOT, 'backend')
const ASSETS = path.join(__dirname, 'assets')
const START_HIDDEN = process.argv.includes('--hidden')
const TOKEN = crypto.randomBytes(24).toString('base64url') // per launch; the backend rejects anything without it

let port = 0
let backend = null
let win = null
let tray = null
let overlay = null
let quitting = false
let settings = {}
let buffer = { active: false, recording: false }
let toldAboutTray = false

app.setAppUserModelId('gg.clutch.desktop')
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required') // overlay sounds

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => showWindow())
  app.whenReady().then(boot).catch((err) => fatal(err))
}

const api = (p) => `http://127.0.0.1:${port}${p}`
async function call(p, init = {}) {
  const res = await fetch(api(p), { ...init, headers: { 'Content-Type': 'application/json', 'X-Clutch-Token': TOKEN, ...(init.headers || {}) } })
  const body = await res.json().catch(() => null)
  if (!res.ok) throw new Error(body?.detail?.message || `HTTP ${res.status}`)
  return body
}

// ── backend ────────────────────────────────────────────────────────────────

async function startBackend() {
  port = await freePort()
  const python = findPython(BACKEND_DIR)
  const log = fs.createWriteStream(path.join(app.getPath('userData'), 'backend.log'), { flags: 'a' })
  log.write(`\n--- ${new Date().toISOString()} starting ${python} on ${port}\n`)
  backend = spawn(python, ['-m', 'clutch.cli', 'serve', '--desktop', '--port', String(port)], {
    cwd: BACKEND_DIR, // so the backend finds its .env (API keys) and stats database
    env: { ...process.env, CLUTCH_TOKEN: TOKEN, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
    windowsHide: true,
  })
  backend.stdout.pipe(log)
  backend.stderr.pipe(log)
  backend.on('exit', (code) => {
    backend = null
    if (!quitting) fatal(new Error(`Clutch's local service stopped (exit ${code}). See ${path.join(app.getPath('userData'), 'backend.log')}`))
  })
  const deadline = Date.now() + 45_000
  while (Date.now() < deadline) {
    try {
      const res = await fetch(api('/api/health'))
      if (res.ok && (await res.json()).desktop) return
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error('Clutch’s local service didn’t start in time. Is the backend installed? (cd backend && pip install -e ".[desktop]")')
}

function fatal(err) {
  if (quitting) return
  dialog.showErrorBox('Clutch', String(err?.message || err))
  quitting = true
  app.quit()
}

// ── windows ────────────────────────────────────────────────────────────────

function createWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1024,
    minHeight: 640,
    show: false,
    backgroundColor: '#09090a',
    title: 'Clutch',
    icon: path.join(ASSETS, 'icon.ico'),
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: '#060607', symbolColor: '#aaa59d', height: 38 },
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, sandbox: true, nodeIntegration: false },
  })
  const base = process.env.CLUTCH_DEV_URL || api('/')
  win.loadURL(base)
  win.once('ready-to-show', () => !START_HIDDEN && win.show())

  // Only Clutch itself renders inside the window; every other link opens in the browser.
  const own = (url) => url.startsWith(api('')) || (process.env.CLUTCH_DEV_URL && url.startsWith(process.env.CLUTCH_DEV_URL))
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (e, url) => {
    if (!own(url)) {
      e.preventDefault()
      if (/^https?:/.test(url)) shell.openExternal(url)
    }
  })
  win.on('close', (e) => {
    if (quitting || settings.minimize_to_tray === false) return
    e.preventDefault()
    win.hide()
    if (!toldAboutTray) {
      toldAboutTray = true
      new Notification({ title: 'Clutch is still running', body: 'Playtime and clipping stay on in the tray. Right-click the tray icon to quit.', icon: path.join(ASSETS, 'icon.png') }).show()
    }
  })
}

function showWindow() {
  if (!win) return
  if (win.isMinimized()) win.restore()
  win.show()
  win.focus()
}

/** A click-through, never-focused toast in the corner of the screen: visible over borderless games. */
function createOverlay() {
  const { workArea } = screen.getPrimaryDisplay()
  overlay = new BrowserWindow({
    width: 380,
    height: 120,
    x: workArea.x + workArea.width - 400,
    y: workArea.y + 20,
    frame: false,
    transparent: true,
    resizable: false,
    movable: false,
    focusable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    show: false,
    hasShadow: false,
    webPreferences: { contextIsolation: true, sandbox: true },
  })
  overlay.setAlwaysOnTop(true, 'screen-saver')
  overlay.setIgnoreMouseEvents(true)
  overlay.setVisibleOnAllWorkspaces(true)
  overlay.loadFile(path.join(__dirname, 'overlay.html'))
}

let overlayTimer = null
function flash(msg) {
  if (!overlay || !msg) return
  overlay.webContents.executeJavaScript(`window.show(${JSON.stringify(msg)})`).catch(() => {})
  overlay.showInactive() // never steal focus from the game
  clearTimeout(overlayTimer)
  overlayTimer = setTimeout(() => overlay && overlay.hide(), msg.tone === 'rec' ? 1800 : 2800)
}

// ── tray & hotkeys ─────────────────────────────────────────────────────────

function trayMenu() {
  return Menu.buildFromTemplate([
    { label: 'Open Clutch', click: showWindow },
    { type: 'separator' },
    { label: `Save clip`, accelerator: settings.hotkey_clip, click: () => action('clip') },
    { label: buffer.recording ? 'Stop recording' : 'Start recording', accelerator: settings.hotkey_record, click: () => action('record') },
    { label: 'Screenshot', accelerator: settings.hotkey_screenshot, click: () => action('screenshot') },
    { label: 'Replay buffer', type: 'checkbox', checked: Boolean(buffer.active), click: (item) => call('/api/desktop/capture/buffer', { method: 'POST', body: JSON.stringify({ on: item.checked }) }).catch(() => {}) },
    { type: 'separator' },
    { label: 'Open clips folder', click: () => settings.clips_dir && shell.openPath(settings.clips_dir) },
    { label: 'Quit Clutch', click: () => { quitting = true; app.quit() } },
  ])
}

function refreshTray() {
  if (!tray) return
  tray.setImage(nativeImage.createFromPath(path.join(ASSETS, buffer.recording ? 'tray-rec.png' : 'tray.png')))
  tray.setToolTip(buffer.recording ? 'Clutch — recording' : buffer.active ? `Clutch — replay buffer on (${settings.hotkey_clip || 'F8'} to clip)` : 'Clutch')
  tray.setContextMenu(trayMenu())
}

async function action(kind) {
  const route = { clip: '/api/desktop/capture/clip', record: '/api/desktop/capture/record', screenshot: '/api/desktop/capture/screenshot' }[kind]
  // Feedback first: the sound and toast land on the key press, not after the file is written.
  if (!(win && win.isFocused())) flash(pressFeedback(kind, settings, buffer.recording))
  try {
    await call(route, { method: 'POST', body: '{}' })
  } catch (err) {
    flash({ title: 'Clutch', sub: err.message, tone: 'error', sound: 'error' })
  }
}

async function registerHotkeys() {
  try {
    settings = await call('/api/desktop/settings')
  } catch {
    return
  }
  globalShortcut.unregisterAll()
  const failed = []
  for (const [key, kind] of [['hotkey_clip', 'clip'], ['hotkey_record', 'record'], ['hotkey_screenshot', 'screenshot']]) {
    const accel = settings[key]
    if (!accel) continue
    try {
      if (!globalShortcut.register(accel, () => action(kind))) failed.push(accel)
    } catch {
      failed.push(accel)
    }
  }
  if (failed.length) flash({ title: 'Hotkey unavailable', sub: `${failed.join(', ')} is taken by another app. Pick another in Settings.`, tone: 'error' })
  refreshTray()
}

// ── live events from the backend ───────────────────────────────────────────

async function followEvents() {
  while (!quitting) {
    try {
      const res = await fetch(api(`/api/desktop/events?token=${encodeURIComponent(TOKEN)}`))
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let pending = ''
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        const { events, rest } = parseSSE(pending + decoder.decode(value, { stream: true }))
        pending = rest
        for (const e of events) onEvent(e)
      }
    } catch {
      /* backend restarting or quitting */
    }
    await new Promise((r) => setTimeout(r, 1500))
  }
}

function onEvent(e) {
  if (e.type === 'buffer' || e.type === 'recording') {
    buffer = { active: e.active, recording: e.recording }
    refreshTray()
  }
  // The in-app toast covers it when Clutch is in front; the overlay is for when you're in game.
  if (!(win && win.isFocused())) flash(overlayFor(e))
  const note = sessionNotification(e)
  if (note) {
    const n = new Notification({ ...note, icon: path.join(ASSETS, 'icon.png') })
    n.on('click', showWindow)
    n.show()
  }
}

// ── renderer bridge ────────────────────────────────────────────────────────

function wireIpc() {
  ipcMain.on('clutch:token', (e) => {
    e.returnValue = e.sender === win?.webContents ? TOKEN : ''
  })
  ipcMain.handle('clutch:pick-exe', async () => {
    const r = await dialog.showOpenDialog(win, { title: 'Add a game', properties: ['openFile'], filters: [{ name: 'Games', extensions: ['exe', 'lnk', 'url', 'bat'] }] })
    return r.canceled ? null : r.filePaths[0]
  })
  ipcMain.handle('clutch:pick-folder', async () => {
    const r = await dialog.showOpenDialog(win, { title: 'Clips folder', properties: ['openDirectory', 'createDirectory'] })
    return r.canceled ? null : r.filePaths[0]
  })
  ipcMain.on('clutch:reload-hotkeys', () => void registerHotkeys())
  ipcMain.on('clutch:login-item', (_e, open) => app.setLoginItemSettings({ openAtLogin: Boolean(open), args: ['--hidden'] }))
  ipcMain.handle('clutch:displays', () => {
    const primary = screen.getPrimaryDisplay().id
    // Desktop Duplication numbers outputs with the primary display first, then left to right.
    return screen
      .getAllDisplays()
      .sort((a, b) => (a.id === primary ? -1 : b.id === primary ? 1 : a.bounds.x - b.bounds.x))
      .map((d) => ({ id: d.id, label: d.label, primary: d.id === primary, width: d.size.width * d.scaleFactor, height: d.size.height * d.scaleFactor }))
  })
}

// ── lifecycle ──────────────────────────────────────────────────────────────

async function boot() {
  wireIpc()
  await startBackend()
  createWindow()
  createOverlay()
  tray = new Tray(nativeImage.createFromPath(path.join(ASSETS, 'tray.png')))
  tray.on('click', showWindow)
  await registerHotkeys()
  followEvents()
}

app.on('before-quit', () => {
  quitting = true
})

app.on('will-quit', (e) => {
  globalShortcut.unregisterAll()
  if (!backend) return
  // Let the backend stop FFmpeg and close sessions cleanly before we exit.
  e.preventDefault()
  const proc = backend
  const done = () => {
    try {
      proc.kill()
    } catch {
      /* already gone */
    }
    backend = null
    app.exit(0)
  }
  call('/api/desktop/shutdown', { method: 'POST' }).catch(() => {})
  proc.once('exit', done)
  setTimeout(done, 4000)
})

app.on('window-all-closed', () => {
  /* stay alive in the tray */
})
