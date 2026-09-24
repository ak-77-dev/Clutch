// One command for the Windows installer: `npm run dist` (or `npm run dist -- --publish` in CI).
//   1. freeze the Python backend with PyInstaller  -> backend/dist/clutch-backend/clutch-backend.exe
//   2. build the React frontend                     -> frontend/dist
//   3. package everything with electron-builder     -> desktop/dist/Clutch-Setup-<version>.exe
'use strict'
const { execFileSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const root = path.resolve(__dirname, '..', '..')
const backend = path.join(root, 'backend')
const frontend = path.join(root, 'frontend')
const desktop = path.join(root, 'desktop')
const publish = process.argv.includes('--publish')
const python = process.env.CLUTCH_PYTHON || [path.join(backend, '.venv', 'Scripts', 'python.exe'), path.join(backend, '.venv', 'bin', 'python')].find((p) => fs.existsSync(p)) || 'python'
const run = (cmd, args, cwd) => {
  console.log(`\n> ${path.basename(cmd)} ${args.join(' ')}`)
  execFileSync(cmd, args, { cwd, stdio: 'inherit', shell: process.platform === 'win32' && !cmd.endsWith('.exe') })
}

run(python, [
  '-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN', '--onedir', '--name', 'clutch-backend',
  '--distpath', 'dist', '--workpath', 'build', '--specpath', 'build',
  '--paths', path.join(root, '..', 'rl-stat-tracker'), '--paths', '.',
  // FFmpeg and PortAudio ship as binaries inside these packages
  '--collect-all', 'imageio_ffmpeg', '--collect-all', 'pyaudiowpatch',
  '--collect-submodules', 'clutch', '--collect-submodules', 'uvicorn', '--collect-submodules', 'rlstats', '--collect-data', 'rlstats',
  '--hidden-import', 'icoextract', '--hidden-import', 'send2trash', '--hidden-import', 'psutil',
  // music controls: WinRT projections (one native module per namespace) and the Core Audio mixer
  '--collect-all', 'winrt', '--collect-submodules', 'pycaw', '--collect-submodules', 'comtypes',
  'packaging/clutch_backend.py',
], backend)

run('npm', ['run', 'build'], frontend)

run('npx', ['electron-builder', '--win', '--x64', '--publish', publish ? 'always' : 'never'], desktop)
console.log(`\nInstaller: ${path.join(desktop, 'dist')}`)
