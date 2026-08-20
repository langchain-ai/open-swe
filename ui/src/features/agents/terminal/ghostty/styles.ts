export function installGhosttyStyles(mount: HTMLElement): void {
  mount.style.position = "relative"
  const style = document.createElement("style")
  style.textContent = `
.open-swe-ghostty-canvas{cursor:text}
.open-swe-ghostty-scrollbar{position:absolute;z-index:1;top:4px;right:1px;bottom:4px;width:8px;cursor:default;touch-action:none}
.open-swe-ghostty-scrollbar-thumb{position:absolute;top:0;right:1px;left:1px;border-radius:3px;background:rgba(148,163,184,.45);transition:background-color 120ms ease-out}
.open-swe-ghostty-scrollbar:hover .open-swe-ghostty-scrollbar-thumb,.open-swe-ghostty-scrollbar:focus-visible .open-swe-ghostty-scrollbar-thumb{background:rgba(148,163,184,.75)}
`
  mount.append(style)
}
