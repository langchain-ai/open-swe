export function installGhosttyStyles(mount: HTMLElement): void {
  mount.style.position = "relative"
  const style = document.createElement("style")
  style.textContent = `
.open-swe-ghostty-canvas{cursor:text}
.open-swe-ghostty-scrollbar{position:absolute;z-index:1;top:4px;right:1px;bottom:4px;width:8px;cursor:default;touch-action:none}
.open-swe-ghostty-scrollbar-thumb{position:absolute;top:0;right:1px;left:1px;border-radius:9999px;background:var(--gtm-line-strong);opacity:.7;transition:opacity 160ms cubic-bezier(0.23,1,0.32,1)}
.open-swe-ghostty-scrollbar:hover .open-swe-ghostty-scrollbar-thumb,.open-swe-ghostty-scrollbar:focus-visible .open-swe-ghostty-scrollbar-thumb{opacity:1}
`
  mount.append(style)
}
