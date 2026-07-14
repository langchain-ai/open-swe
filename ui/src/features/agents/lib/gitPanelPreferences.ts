const PANEL_STORAGE_COLLAPSED = "open-swe.gitpanel.collapsed"
const COLLAPSED_STATE_TRUE = "1"
const COLLAPSED_STATE_FALSE = "0"
const PANEL_DEFAULT_EXPANDED_MEDIA_QUERY = "(min-width: 1536px)"

export function readStoredPanelCollapsed(): boolean {
  if (typeof window === "undefined") return true
  const stored = window.localStorage.getItem(PANEL_STORAGE_COLLAPSED)
  if (stored === COLLAPSED_STATE_TRUE) return true
  if (stored === COLLAPSED_STATE_FALSE) return false
  return !window.matchMedia(PANEL_DEFAULT_EXPANDED_MEDIA_QUERY).matches
}

export function writeStoredPanelCollapsed(collapsed: boolean): void {
  if (typeof window === "undefined") return
  window.localStorage.setItem(
    PANEL_STORAGE_COLLAPSED,
    collapsed ? COLLAPSED_STATE_TRUE : COLLAPSED_STATE_FALSE
  )
}
