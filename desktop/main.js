// Clutch desktop shell: runs the Python backend, hosts the UI, owns the tray,
// global hotkeys and the in-game overlay.
'use strict'
const { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain, dialog, screen, Notification, shell, nativeImage } = require('electron')
const { spawn } = require('node:child_process')
const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { findPython, musicFeedback, parseSSE, overlayFor, pressFeedback, sessionNotification, eventNotification } = require('./lib')
const { DiscordPresence, activityFor } = require('./discord')

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
let panel = null
let panelShown = false
let discord = null
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

/** How to run the backend: the bundled exe in an installed build, the repo's venv in development. */
function backendCommand() {
  const args = ['serve', '--desktop', '--port', '0'] // the backend binds a free port and reports it
  if (app.isPackaged) {
    const home = path.join(app.getPath('appData'), 'Clutch') // API keys (.env) and the stats database live here
    fs.mkdirSync(home, { recursive: true })
    return {
      cmd: path.join(process.resourcesPath, 'backend', 'clutch-backend.exe'),
      args,
      cwd: home,
      env: { CLUTCH_STATIC_DIR: path.join(process.resourcesPath, 'frontend'), CLUTCH_DB: path.join(home, 'clutch.db') },
    }
  }
  return { cmd: findPython(BACKEND_DIR), args: ['-m', 'clutch.cli', ...args], cwd: BACKEND_DIR, env: {} }
}

const ALREADY_RUNNING = 75 // the backend's exit code when another backend owns the data folder
const LOG_PATH = () => path.join(app.getPath('userData'), 'backend.log')
const DATA_DIR = () => process.env.CLUTCH_HOME || path.join(app.getPath('appData'), 'Clutch')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

let backendPid = null
let adopted = false

/**
 * Start the backend and wait until it answers with our token.
 *
 * The backend binds its own port and prints `CLUTCH_PORT <n>`: choosing a free port here and
 * polling it while a slow first launch warms up raced on Windows (a connect to a port nobody
 * listens on can bind to that very port, and the backend then can't).
 *
 * The backend also locks its data folder, so a second copy exits with ALREADY_RUNNING. Then
 * `backend.pid` names the owner: if it answers with our token we adopt it, otherwise it's a
 * leftover from an earlier launch, so we stop it and start ours.
 */
async function startBackend() {
  const log = fs.createWriteStream(LOG_PATH(), { flags: 'a' })
  for (let attempt = 1; attempt <= 2; attempt++) {
    const run = backendCommand()
    log.write(`\n--- ${new Date().toISOString()} starting ${run.cmd}\n`)
    const child = spawn(run.cmd, run.args, {
      cwd: run.cwd, // the backend reads its .env (API keys) and stats database from here
      env: { ...process.env, ...run.env, CLUTCH_TOKEN: TOKEN, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
      windowsHide: true,
    })
    backend = child
    child.stdout.pipe(log, { end: false })
    child.stderr.pipe(log, { end: false })
    child.on('exit', (code) => {
      if (backend === child) backend = null
      if (quitting || code === ALREADY_RUNNING || !backendPid || adopted) return
      fatal(new Error(`Clutch's local service stopped (exit ${code}). See ${LOG_PATH()}`))
    })

    const reported = await reportedPort(child)
    if (reported) {
      port = reported
      const health = await waitForHealth(() => child.exitCode === null)
      if (health && (await tokenAccepted())) {
        backendPid = health.pid
        return
      }
      break // ours started but never became healthy: the log says why
    }
    if (child.exitCode !== ALREADY_RUNNING) break

    const owner = readPidFile()
    if (owner) {
      port = owner.port
      const health = await waitForHealth(() => processAlive(owner.pid), 8000)
      if (health && (await tokenAccepted())) {
        backendPid = health.pid
        adopt(health.pid, log)
        return
      }
      log.write(`--- stopping a Clutch backend left over from an earlier launch (pid ${owner.pid})\n`)
      try {
        process.kill(owner.pid)
      } catch {
        /* already gone */
      }
    }
    await sleep(1500)
  }
  throw new Error(`Clutch’s local service didn’t start. See ${LOG_PATH()}`)
}

/** Resolves with the port the backend printed, or null if it exits first (or takes over 90 s). */
function reportedPort(child) {
  return new Promise((resolve) => {
    let text = ''
    const done = (value) => {
      clearTimeout(timer)
      child.stdout.off('data', onData)
      child.off('exit', onExit)
      resolve(value)
    }
    const onData = (chunk) => {
      text += chunk
      const m = /CLUTCH_PORT (\d+)/.exec(text)
      if (m) done(Number(m[1]))
    }
    const onExit = () => done(null)
    const timer = setTimeout(() => done(null), 90_000) // a first launch can be slow while antivirus scans the new exe
    child.stdout.on('data', onData)
    child.on('exit', onExit)
  })
}

/** Poll /api/health on `port` until a desktop backend answers, while `alive()` holds. */
async function waitForHealth(alive, timeoutMs = 45_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline && alive()) {
    try {
      const res = await fetch(api('/api/health'))
      const body = res.ok ? await res.json() : null
      if (body?.desktop) return body
    } catch {
      /* not up yet */
    }
    await sleep(250)
  }
  return null
}

async function tokenAccepted() {
  try {
    await call('/api/desktop/settings')
    return true
  } catch {
    return false
  }
}

function readPidFile() {
  try {
    const [pid, p] = fs.readFileSync(path.join(DATA_DIR(), 'backend.pid'), 'utf8').trim().split(/\s+/).map(Number)
    return pid && p && pid !== process.pid ? { pid, port: p } : null
  } catch {
    return null
  }
}

function processAlive(pid) {
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

/** Another backend owns the data folder and answers with our token: use it, and watch it like our own. */
function adopt(pid, log) {
  adopted = true
  log.write(`--- using the Clutch backend already running (pid ${pid}, port ${port})\n`)
  const timer = setInterval(() => {
    if (processAlive(pid)) return
    clearInterval(timer)
    if (!quitting) fatal(new Error(`Clutch's local service stopped. See ${LOG_PATH()}`))
  }, 5000)
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

/** The in-game session panel: time played, today's record, clips, daily cap. Click-through, never focused. */
function createPanel() {
  const { workArea } = screen.getPrimaryDisplay()
  panel = new BrowserWindow({
    width: 310,
    height: 230, // room for the now-playing row
    x: workArea.x + 8,
    y: workArea.y + 8,
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
  panel.setAlwaysOnTop(true, 'screen-saver')
  panel.setIgnoreMouseEvents(true)
  panel.loadFile(path.join(__dirname, 'overlay-panel.html'))
}

async function refreshPanel() {
  if (!panel) return
  let session = null
  try {
    session = await call('/api/desktop/session')
  } catch {
    return
  }
  updatePresence(session)
  const visible = Boolean(settings.overlay_enabled && panelShown && session?.playing)
  panel.webContents.executeJavaScript(`window.render(${JSON.stringify(session)}, ${JSON.stringify(settings.hotkey_overlay || '')})`).catch(() => {})
  if (visible && !panel.isVisible()) panel.showInactive()
  if (!visible && panel.isVisible()) panel.hide()
}

// ── Discord Rich Presence ──────────────────────────────────────────────────

let presenceStart = null
async function syncDiscord() {
  const want = settings.discord_rpc && settings.discord_client_id
  if (!want) {
    if (discord) discord.close()
    discord = null
    return
  }
  if (discord && discord.clientId === settings.discord_client_id && discord.ready) return
  if (discord) discord.close()
  discord = new DiscordPresence(settings.discord_client_id)
  const ok = await discord.connect()
  if (!ok) flash({ title: 'Discord', sub: `Rich Presence: ${discord.lastError}`, tone: 'error' })
}

function updatePresence(session) {
  if (!discord?.ready) return
  const game = session?.playing
  if (!game) {
    presenceStart = null
    discord.clear()
    return
  }
  presenceStart = presenceStart || game.started_at
  discord.setActivity(activityFor(session, presenceStart))
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
    { label: 'Session panel in game', type: 'checkbox', checked: Boolean(settings.overlay_enabled), accelerator: settings.hotkey_overlay, click: () => togglePanel() },
    { label: 'Open clips folder', click: () => settings.clips_dir && shell.openPath(settings.clips_dir) },
    ...(app.isPackaged ? [{ label: 'Check for updates', click: () => checkForUpdates(true) }] : []),
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

/** Media hotkeys: control whatever's playing, then say what's on now (over the game). */
async function mediaAction(which) {
  try {
    const state = await call(`/api/desktop/media/${which}`, { method: 'POST', body: '{}' })
    if (!(win && win.isFocused())) flash(musicFeedback(which, state))
    setTimeout(refreshPanel, 300)
  } catch (err) {
    flash({ title: 'Music', sub: err.message, tone: 'error', sound: null })
  }
}

async function togglePanel() {
  if (!settings.overlay_enabled) {
    try {
      settings = await call('/api/desktop/settings', { method: 'PUT', body: JSON.stringify({ overlay_enabled: true }) })
    } catch {
      return
    }
    panelShown = true
  } else {
    panelShown = !panelShown
  }
  refreshTray()
  refreshPanel()
}

async function registerHotkeys() {
  try {
    settings = await call('/api/desktop/settings')
  } catch {
    return
  }
  globalShortcut.unregisterAll()
  const failed = []
  const bindings = [
    ['hotkey_clip', 'clip'],
    ['hotkey_record', 'record'],
    ['hotkey_screenshot', 'screenshot'],
    ['hotkey_overlay', 'panel'],
    ['hotkey_media_play', 'media:play_pause'],
    ['hotkey_media_next', 'media:next'],
    ['hotkey_media_prev', 'media:previous'],
  ]
  for (const [key, kind] of bindings) {
    const accel = settings[key]
    if (!accel) continue
    try {
      const handler = kind === 'panel' ? () => togglePanel() : kind.startsWith('media:') ? () => mediaAction(kind.slice(6)) : () => action(kind)
      if (!globalShortcut.register(accel, handler)) failed.push(accel)
    } catch {
      failed.push(accel)
    }
  }
  syncDiscord()
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
  const note = sessionNotification(e) || eventNotification(e)
  if (e.type === 'game_started') {
    panelShown = true
    setTimeout(refreshPanel, 1500)
  }
  if (e.type === 'session_end' || e.type === 'clip_saved') setTimeout(refreshPanel, 500)
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
  ipcMain.on('clutch:reload-hotkeys', () => void registerHotkeys()) // also re-reads overlay + Discord settings
  ipcMain.on('clutch:app-info', (e) => {
    e.returnValue = { version: app.getVersion(), packaged: app.isPackaged }
  })
  ipcMain.on('clutch:check-updates', () => app.isPackaged && checkForUpdates(true))
  ipcMain.on('clutch:open-external', (_e, url) => {
    if (/^https:\/\//.test(String(url))) shell.openExternal(String(url))
  })
  ipcMain.on('clutch:copy', (_e, text) => require('electron').clipboard.writeText(String(text).slice(0, 2000)))
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
  createPanel()
  tray = new Tray(nativeImage.createFromPath(path.join(ASSETS, 'tray.png')))
  tray.on('click', showWindow)
  await registerHotkeys()
  followEvents()
  setInterval(refreshPanel, 5000)
  if (app.isPackaged) setTimeout(() => checkForUpdates(false), 15_000)
}

// ── updates (installed builds only) ────────────────────────────────────────

function checkForUpdates(manual) {
  let autoUpdater
  try {
    ;({ autoUpdater } = require('electron-updater'))
  } catch {
    return
  }
  autoUpdater.autoDownload = true
  autoUpdater.once('update-downloaded', (info) => {
    const n = new Notification({ title: `Clutch ${info.version} is ready`, body: 'Restart Clutch to update. It installs in a few seconds.', icon: path.join(ASSETS, 'icon.png') })
    n.on('click', () => {
      quitting = true
      autoUpdater.quitAndInstall()
    })
    n.show()
  })
  if (manual) {
    autoUpdater.once('update-not-available', () => new Notification({ title: 'Clutch is up to date', body: `You're on ${app.getVersion()}.` }).show())
    autoUpdater.once('error', (err) => new Notification({ title: 'Couldn’t check for updates', body: String(err?.message || err).slice(0, 120) }).show())
  }
  autoUpdater.checkForUpdates().catch(() => {})
}

app.on('before-quit', () => {
  quitting = true
  if (discord) discord.close()
})

app.on('will-quit', (e) => {
  globalShortcut.unregisterAll()
  if (!backend && adopted && backendPid) {
    // An adopted backend isn't our child: ask it to stop, then make sure.
    e.preventDefault()
    const pid = backendPid
    backendPid = null
    call('/api/desktop/shutdown', { method: 'POST' }).catch(() => {})
    setTimeout(() => {
      try {
        process.kill(pid)
      } catch {
        /* already gone */
      }
      app.exit(0)
    }, 1500)
    return
  }
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
