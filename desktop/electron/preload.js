'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktop', {
  isDesktop: true,
  retry: () => ipcRenderer.send('desktop:retry'),
  openLogs: () => ipcRenderer.send('desktop:open-logs'),
});
