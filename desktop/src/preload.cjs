const DRAG_REGION_ID = "open-swe-desktop-drag-region"

window.addEventListener("DOMContentLoaded", () => {
  if (process.platform !== "darwin") return

  // The hosted UI cannot detect its Electron host. Mirror T3 Code's 52px title bar and 90px
  // traffic-light inset here while keeping the header's interactive descendants clickable.
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
  `
  document.head.append(style)

  const dragRegion = document.createElement("div")
  dragRegion.id = DRAG_REGION_ID
  dragRegion.setAttribute("aria-hidden", "true")
  document.body.append(dragRegion)
})
