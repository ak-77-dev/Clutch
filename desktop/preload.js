// The only bridge between the UI and Electron: a token for the local API plus a few native dialogs.
'use strict'
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('clutchDesktop', {
  token: ipcRenderer.sendSync('clutch:token'),
  platform: process.platform,
  version: '0.2.0',
  pickExecutable: () => ipcRenderer.invoke('clutch:pick-exe'),
  pickFolder: () => ipcRenderer.invoke('clutch:pick-folder'),
  reloadHotkeys: () => ipcRenderer.send('clutch:reload-hotkeys'),
  setLoginItem: (open) => ipcRenderer.send('clutch:login-item', open),
  displays: () => ipcRenderer.invoke('clutch:displays'),
})
