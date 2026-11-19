import {
  useRouter,
  useRouterState,
  type AnyRouter,
  type ParsedLocation,
} from "@tanstack/react-router"

import { parsePullRequestSelection } from "@/features/reviews/search"

import { getLastAppLocation, sectionOf } from "./appLocation"

type ContextKind = "review" | "settings"

type Origin = {
  key: string
  index: number
  href: string
  label: string
}

type Entry = {
  index: number
  origin: Origin | null
}

const STORAGE_KEY = "open-swe.context-navigation.v1"
const MAX_ENTRIES = 100
const REVIEW_PAGE = /^\/agents\/reviews\/([^/]+)\/([^/]+)\/(\d+)\/?$/
const SETTINGS_ROOTS = [
  "/my-settings",
  "/review",
  "/admin",
  "/usage",
  "/integrations",
  "/feature-flags",
  "/agents/instructions",
]

export class ContextNavigation {
  private static instances = new WeakMap<AnyRouter, ContextNavigation>()
  private entries = new Map<string, Entry>()

  private constructor(private router: AnyRouter) {
    this.restore()
    router.subscribe("onBeforeNavigate", ({ fromLocation, toLocation }) => {
      this.capture(fromLocation, toLocation)
    })
  }

  static for(router: AnyRouter): ContextNavigation {
    let navigation = this.instances.get(router)
    if (!navigation) {
      navigation = new ContextNavigation(router)
      this.instances.set(router, navigation)
    }
    return navigation
  }

  private key(location: ParsedLocation): string | undefined {
    return location.state.__TSR_key ?? location.state.key
  }

  private under(pathname: string, root: string): boolean {
    return pathname === root || pathname.startsWith(`${root}/`)
  }

  private review(href: string): string | null {
    const url = new URL(href, "https://robin.invalid")
    const legacy = REVIEW_PAGE.exec(url.pathname)
    if (legacy) return `${legacy[1]}/${legacy[2]}#${legacy[3]}`
    if (!/^\/agents\/reviews\/?$/.test(url.pathname)) return null
    const pr = parsePullRequestSelection(
      url.searchParams.get("pr") ?? undefined
    )
    return pr ? `${pr.owner}/${pr.repo}#${pr.number}` : null
  }

  private kind(href: string): ContextKind | null {
    if (this.review(href)) return "review"
    const pathname = new URL(href, "https://robin.invalid").pathname
    return SETTINGS_ROOTS.some((root) => this.under(pathname, root))
      ? "settings"
      : null
  }

  private safe(href: string): boolean {
    if (!href.startsWith("/") || href.startsWith("//") || /[\\\s]/.test(href))
      return false
    const pathname = new URL(href, "https://robin.invalid").pathname
    return (
      this.under(pathname, "/agents") ||
      this.under(pathname, "/assistant") ||
      this.kind(pathname) === "settings"
    )
  }

  private label(href: string): string {
    const review = this.review(href)
    if (review) return `Back to ${review}`
    const pathname = new URL(href, "https://robin.invalid").pathname
    if (this.under(pathname, "/agents/reviews")) return "Back to pull requests"
    if (pathname === "/agents" || pathname === "/agents/")
      return "Back to threads"
    if (
      !sectionOf(pathname) &&
      /^\/(?:agents|assistant)\/[^/]+\/?$/.test(pathname)
    )
      return "Back to thread"
    return "Back to previous page"
  }

  private origin(location: ParsedLocation): Origin | null {
    const key = this.key(location)
    if (!key || !this.safe(location.href)) return null
    return {
      key,
      index: location.state.__TSR_index,
      href:
        sectionOf(location.pathname) === "/agents/connections"
          ? location.pathname
          : location.href,
      label: this.label(location.href),
    }
  }

  private capture(from: ParsedLocation | undefined, to: ParsedLocation) {
    const key = this.key(to)
    if (!key || this.entries.has(key)) return
    const fromKey = from && this.key(from)
    if (from && fromKey && !this.entries.has(fromKey))
      this.entries.set(fromKey, { index: from.state.__TSR_index, origin: null })
    const index = to.state.__TSR_index
    const previous =
      this.entries.get(from ? (this.key(from) ?? "") : "")?.origin ?? null
    for (const [entryKey, entry] of this.entries)
      if (entry.index >= index) this.entries.delete(entryKey)
    let origin: Origin | null = null
    const kind = this.kind(to.href)
    if (kind && from) {
      origin =
        to.state.contextNavigationOrigin !== undefined
          ? to.state.contextNavigationOrigin
          : (kind === "settings" && this.kind(from.href) === "settings") ||
              (kind === "review" &&
                (this.review(from.href) === this.review(to.href) ||
                  this.kind(from.href) === "settings"))
            ? previous
            : this.origin(from)
      if (origin?.href === to.href) origin = null
    }
    this.entries.set(key, { index, origin })
    while (this.entries.size > MAX_ENTRIES) {
      const oldest = this.entries.keys().next().value
      if (oldest !== undefined) this.entries.delete(oldest)
    }
    this.persist()
  }

  private validOrigin(value: unknown): value is Origin {
    return (
      typeof value === "object" &&
      value !== null &&
      "key" in value &&
      typeof value.key === "string" &&
      "index" in value &&
      Number.isInteger(value.index) &&
      "href" in value &&
      typeof value.href === "string" &&
      this.safe(value.href) &&
      "label" in value &&
      typeof value.label === "string"
    )
  }

  private restore() {
    if (typeof window === "undefined") return
    try {
      const stored: unknown = JSON.parse(
        window.sessionStorage.getItem(STORAGE_KEY) ?? "[]"
      )
      if (!Array.isArray(stored)) return
      for (const entry of stored.slice(-MAX_ENTRIES)) {
        if (!Array.isArray(entry) || typeof entry[0] !== "string") continue
        const value: unknown = entry[1]
        if (
          typeof value !== "object" ||
          value === null ||
          !("index" in value) ||
          typeof value.index !== "number" ||
          !Number.isInteger(value.index) ||
          !("origin" in value) ||
          (value.origin !== null && !this.validOrigin(value.origin))
        )
          continue
        this.entries.set(entry[0], { index: value.index, origin: value.origin })
      }
    } catch (error) {
      console.warn("Could not restore navigation origins", error)
    }
  }

  private persist() {
    if (typeof window === "undefined") return
    try {
      window.sessionStorage.setItem(
        STORAGE_KEY,
        JSON.stringify([...this.entries])
      )
    } catch (error) {
      console.warn("Could not save navigation origins", error)
    }
  }

  returnOptions(kind: ContextKind, location: ParsedLocation) {
    const origin = this.entries.get(this.key(location) ?? "")?.origin ?? null
    return {
      label:
        origin?.label ??
        (kind === "review" ? "Pull requests" : "Close settings"),
      back: origin !== null,
      state: { contextNavigationOrigin: origin },
      returnToOrigin: () => {
        if (origin && this.safe(origin.href)) {
          const delta = origin.index - location.state.__TSR_index
          if (delta && this.entries.get(origin.key)?.index === origin.index) {
            this.router.history.go(delta)
            return
          }
        }
        const fallback =
          kind === "review" ? "/agents/reviews" : getLastAppLocation()
        const href = origin?.href ?? fallback
        void this.router.navigate({
          href: this.safe(href) ? href : "/agents",
          replace: true,
        })
      },
    }
  }
}

export function useContextReturn(kind: ContextKind) {
  const router = useRouter()
  const location = useRouterState({ select: (state) => state.location })
  return ContextNavigation.for(router).returnOptions(kind, location)
}

declare module "@tanstack/react-router" {
  interface HistoryState {
    contextNavigationOrigin?: Origin | null
  }
}
