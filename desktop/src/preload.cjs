const { contextBridge, ipcRenderer } = require("electron")

contextBridge.exposeInMainWorld("openSweDesktop", {
  isDesktop: true,
  listProjects: () => ipcRenderer.invoke("desktop:projects"),
  getProjectBranch: (cwd) => ipcRenderer.invoke("desktop:project-branch", cwd),
  addProject: () => ipcRenderer.invoke("desktop:add-project"),
  removeProject: (cwd) => ipcRenderer.invoke("desktop:remove-project", cwd),
  openExternal: (url) => ipcRenderer.invoke("desktop:open-external", url),
  resolveAcpProjectPath: (input) =>
    ipcRenderer.invoke("desktop:resolve-acp-project-path", { ...input }),
  onProjectsChanged: (callback) => {
    const listener = (_event, projects) => callback(projects)
    ipcRenderer.on("desktop:projects-changed", listener)
    return () => ipcRenderer.removeListener("desktop:projects-changed", listener)
  },
  startAcpSession: (input) => ipcRenderer.invoke("desktop:acp-start", input),
  promptAcpSession: (input) => ipcRenderer.invoke("desktop:acp-prompt", input),
  cancelAcpSession: (sessionId) => ipcRenderer.invoke("desktop:acp-cancel", sessionId),
  deleteAcpSession: (sessionId) => ipcRenderer.invoke("desktop:acp-delete", sessionId),
  getAcpSession: (sessionId) => ipcRenderer.invoke("desktop:acp-session", sessionId),
  listAcpSessions: () => ipcRenderer.invoke("desktop:acp-sessions"),
  getAcpDiff: (sessionId) => ipcRenderer.invoke("desktop:acp-diff", sessionId),
  onAcpEvent: (callback) => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on("desktop:acp-event", listener)
    return () => ipcRenderer.removeListener("desktop:acp-event", listener)
  },
  terminal: {
    attach: (input) => ipcRenderer.invoke("desktop:terminal-attach", { ...input }),
    open: (input) => ipcRenderer.invoke("desktop:terminal-attach", { ...input }),
    write: (input) => ipcRenderer.invoke("desktop:terminal-write", { ...input }),
    resize: (input) => ipcRenderer.invoke("desktop:terminal-resize", { ...input }),
    clear: (input) => ipcRenderer.invoke("desktop:terminal-clear", { ...input }),
    restart: (input) => ipcRenderer.invoke("desktop:terminal-restart", { ...input }),
    detach: (input) => ipcRenderer.invoke("desktop:terminal-detach", { ...input }),
    close: (input) => ipcRenderer.invoke("desktop:terminal-close", { ...input }),
    list: (localSessionId) => ipcRenderer.invoke("desktop:terminal-list", localSessionId),
    subscribeMetadata: (localSessionId) =>
      ipcRenderer.invoke("desktop:terminal-metadata-subscribe", localSessionId),
    detachMetadata: (localSessionId) =>
      ipcRenderer.invoke("desktop:terminal-metadata-detach", localSessionId),
    onEvent: (callback) => {
      const listener = (_event, terminalEvent) => callback(terminalEvent)
      ipcRenderer.on("desktop:terminal-event", listener)
      return () => ipcRenderer.removeListener("desktop:terminal-event", listener)
    },
    onMetadata: (callback) => {
      const listener = (_event, metadataEvent) => callback(metadataEvent)
      ipcRenderer.on("desktop:terminal-metadata", listener)
      return () => ipcRenderer.removeListener("desktop:terminal-metadata", listener)
    },
  },
})

const DRAG_REGION_ID = "open-swe-desktop-drag-region"

window.addEventListener("DOMContentLoaded", () => {
  if (process.platform !== "darwin") return

  // Keep a draggable strip beside the native controls without forcing sidebar
  // content into the titlebar row.
  const style = document.createElement("style")
  style.textContent = `
    #${DRAG_REGION_ID} {
      -webkit-app-region: drag;
      position: fixed;
      top: 0;
      left: 90px;
      right: 0;
      height: 12px;
      z-index: 2147483647;
      user-select: none;
    }

    [data-sidebar-expand] {
      -webkit-app-region: no-drag;
      left: 90px !important;
    }
  `
  document.head.append(style)

  const dragRegion = document.createElement("div")
  dragRegion.id = DRAG_REGION_ID
  dragRegion.setAttribute("aria-hidden", "true")
  document.body.append(dragRegion)
})
