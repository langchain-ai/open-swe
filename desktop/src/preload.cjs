const { contextBridge, ipcRenderer } = require("electron")

contextBridge.exposeInMainWorld("openSweDesktop", {
  isDesktop: true,
  pickDirectory: () => ipcRenderer.invoke("desktop:pick-directory"),
  startAcpSession: (input) => ipcRenderer.invoke("desktop:acp-start", input),
  promptAcpSession: (input) => ipcRenderer.invoke("desktop:acp-prompt", input),
  cancelAcpSession: (sessionId) => ipcRenderer.invoke("desktop:acp-cancel", sessionId),
  getAcpSession: (sessionId) => ipcRenderer.invoke("desktop:acp-session", sessionId),
  listAcpSessions: () => ipcRenderer.invoke("desktop:acp-sessions"),
  onAcpEvent: (callback) => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on("desktop:acp-event", listener)
    return () => ipcRenderer.removeListener("desktop:acp-event", listener)
  },
})

const DRAG_REGION_ID = "open-swe-desktop-drag-region"

window.addEventListener("DOMContentLoaded", () => {
  if (process.platform !== "darwin") return

  // Mirror T3 Code's 52px title bar and 90px traffic-light inset while keeping the header's
  // interactive descendants clickable.
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

    aside > div:first-child {
      -webkit-app-region: drag;
      box-sizing: border-box;
      height: 52px;
      min-height: 52px;
      padding-left: 90px !important;
      padding-top: 0 !important;
      padding-bottom: 0 !important;
    }

    aside > div:first-child :is(a, button, input, textarea, select, [role="button"]) {
      -webkit-app-region: no-drag;
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
