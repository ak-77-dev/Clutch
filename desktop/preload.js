// The only bridge between the UI and Electron: a token for the local API plus a few native dialogs.
'use strict'
const { contextBridge, ipcRenderer } = require('electron')

const info = ipcRenderer.sendSync('clutch:app-info')

contextBridge.exposeInMainWorld('clutchDesktop', {
  token: ipcRenderer.sendSync('clutch:token'),
  platform: process.platform,
  version: info.version,
  packaged: info.packaged,
  checkUpdates: () => ipcRenderer.send('clutch:check-updates'),
  pickExecutable: () => ipcRenderer.invoke('clutch:pick-exe'),
  pickFolder: () => ipcRenderer.invoke('clutch:pick-folder'),
  reloadHotkeys: () => ipcRenderer.send('clutch:reload-hotkeys'),
  setLoginItem: (open) => ipcRenderer.send('clutch:login-item', open),
  displays: () => ipcRenderer.invoke('clutch:displays'),
  openExternal: (url) => ipcRenderer.send('clutch:open-external', url),
  copy: (text) => ipcRenderer.send('clutch:copy', text),
})
