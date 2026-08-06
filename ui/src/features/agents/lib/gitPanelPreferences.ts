const PANEL_STORAGE_COLLAPSED = "open-swe.gitpanel.collapsed"
const COLLAPSED_STATE_TRUE = "1"
const COLLAPSED_STATE_FALSE = "0"

function panelStorageKey(threadId: string): string {
  return `${PANEL_STORAGE_COLLAPSED}.${threadId}`
}

export function readStoredPanelCollapsed(threadId: string): boolean {
  if (typeof window === "undefined") return true
  return (
    window.localStorage.getItem(panelStorageKey(threadId)) !==
    COLLAPSED_STATE_FALSE
  )
}

export function writeStoredPanelCollapsed(
  threadId: string,
  collapsed: boolean
): void {
  if (typeof window === "undefined") return
  window.localStorage.setItem(
    panelStorageKey(threadId),
    collapsed ? COLLAPSED_STATE_TRUE : COLLAPSED_STATE_FALSE
  )
}
