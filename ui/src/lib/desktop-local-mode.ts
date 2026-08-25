export function isDesktopLocalModeEnabled(): boolean {
  return (
    typeof window !== "undefined" &&
    window.openSweDesktop?.localOnly === true
  )
}

export function enableDesktopLocalMode(): void {
  // Local-only mode is selected by the desktop process at startup.
}
