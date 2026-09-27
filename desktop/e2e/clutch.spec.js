// Opens every page of the real app, in a throwaway profile, and fails on crashes,
// error screens and console errors. Screenshots of each page land in e2e-results/.
'use strict'
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { test, expect, _electron: electron } = require('@playwright/test')

const PAGES = [
  ['home', '/'],
  ['library', '/library'],
  ['clips', '/clips'],
  ['playtime', '/playtime'],
  ['sessions', '/sessions'],
  ['goals', '/goals'],
  ['friends', '/friends'],
  ['music', '/music'],
  ['stats', '/stats'],
  ['lol-demo', '/lol/p/demo-lol-nightfall'],
  ['settings', '/settings'],
]

let app
let win
const problems = []

test.beforeAll(async () => {
  // Never the player's real data: a fresh profile, data folder, database and keys file.
  const home = fs.mkdtempSync(path.join(process.env.CLUTCH_E2E_TMP || os.tmpdir(), 'clutch-e2e-'))
  fs.mkdirSync(path.join(home, 'data'))
  fs.writeFileSync(
    path.join(home, 'data', 'settings.json'),
    JSON.stringify({ onboarded: true, clips_dir: path.join(home, 'clips'), auto_buffer: false }),
  )
  app = await electron.launch({
    args: [path.join(__dirname, '..')],
    env: {
      ...process.env,
      CLUTCH_TEST: '1',
      CLUTCH_USER_DATA: path.join(home, 'profile'),
      CLUTCH_HOME: path.join(home, 'data'),
      CLUTCH_DB: path.join(home, 'data', 'clutch.db'),
      CLUTCH_ENV_FILE: path.join(home, 'data', '.env'),
    },
  })
  // The main window is the one that loads the app over http (the overlays are local files).
  win = await app.firstWindow()
  for (let i = 0; i < 50 && !win.url().startsWith('http'); i++) {
    const found = app.windows().find((w) => w.url().startsWith('http'))
    if (found) win = found
    else await new Promise((r) => setTimeout(r, 200))
  }
  win.on('console', (m) => m.type() === 'error' && problems.push(`console: ${m.text()}`))
  win.on('pageerror', (e) => problems.push(`page error: ${e.message}`))
  await win.waitForSelector('nav.rail', { timeout: 120_000 })
})

test.afterAll(async () => {
  await app?.close()
})

for (const [name, route] of PAGES) {
  test(`${name} page renders without errors`, async () => {
    problems.length = 0
    await win.evaluate((r) => {
      window.history.pushState({}, '', r)
      window.dispatchEvent(new PopStateEvent('popstate'))
    }, route)
    await win.waitForLoadState('networkidle').catch(() => {})
    // Wait for the data, not just the route: loading placeholders must all be gone.
    await expect(win.locator('.skeleton')).toHaveCount(0, { timeout: 60_000 })
    await win.waitForTimeout(500) // let entrance animations settle before the screenshot
    await expect(win.locator('text=Page not found')).toHaveCount(0)
    await expect(win.locator('.card.empty.error, .error-boundary')).toHaveCount(0)
    await win.screenshot({ path: path.join(__dirname, '..', 'e2e-results', `${name}.png`) })
    // Artwork that a CDN doesn't have is expected; anything else in the console is a bug.
    const real = problems.filter((p) => !/Failed to load resource.*(404|net::ERR)/.test(p))
    expect(real, real.join('\n')).toEqual([])
  })
}

test('settings shows the API keys and sync controls', async () => {
  await win.evaluate(() => {
    window.history.pushState({}, '', '/settings')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  await expect(win.getByRole('heading', { name: 'API keys' })).toBeVisible()
  await expect(win.getByText('Sync to your other PCs')).toBeVisible()
})
