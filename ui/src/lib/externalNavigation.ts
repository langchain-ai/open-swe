import { createStore } from "zustand/vanilla"

class ExternalNavigation {
  readonly pending = createStore<{
    url: string
    target: string
    trigger: HTMLElement | null
  } | null>(() => null)

  isGitHub(url: string) {
    if (!URL.canParse(url, window.location.href)) return false
    const parsed = new URL(url, window.location.href)
    return (
      (parsed.protocol === "https:" || parsed.protocol === "http:") &&
      (parsed.hostname === "github.com" || parsed.hostname === "www.github.com")
    )
  }

  open(
    url: string,
    target = "_blank",
    trigger = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
  ) {
    if (!window.openSweDesktop && this.isGitHub(url)) {
      this.pending.setState({ url, target, trigger }, true)
      return
    }
    this.navigate(url, target)
  }

  navigate(url: string, target: string) {
    if (target === "_self") window.location.assign(url)
    else window.open(url, target, "noopener,noreferrer")
  }

  confirm() {
    const pending = this.pending.getState()
    this.pending.setState(null, true)
    if (pending) this.navigate(pending.url, pending.target)
  }

  dismiss() {
    this.pending.setState(null, true)
  }

  capture(event: MouseEvent) {
    if (window.openSweDesktop || event.button > 1) return
    const anchor =
      event.target instanceof Element
        ? event.target.closest<HTMLAnchorElement>("a[href]")
        : null
    if (
      !anchor ||
      anchor.hasAttribute("download") ||
      !this.isGitHub(anchor.href)
    )
      return
    event.preventDefault()
    event.stopImmediatePropagation()
    this.open(
      anchor.href,
      event.button === 1 || event.metaKey || event.ctrlKey || event.shiftKey
        ? "_blank"
        : anchor.target || "_self",
      anchor
    )
  }
}

export const externalNavigation = new ExternalNavigation()
