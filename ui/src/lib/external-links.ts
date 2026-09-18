export function openExternalLinksInNewWindow(event: MouseEvent) {
  const anchor = (event.target as Element | null)?.closest("a[href]")
  if (!(anchor instanceof HTMLAnchorElement) || anchor.target) return

  const url = new URL(anchor.href, window.location.href)
  if (url.protocol !== "http:" && url.protocol !== "https:") return
  if (url.origin === window.location.origin) return

  anchor.target = "_blank"
  anchor.rel = "noopener noreferrer"
}
