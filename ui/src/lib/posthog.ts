import type { CaptureResult, PostHog, Properties } from "posthog-js"

import type { SessionUser } from "./api"
import { TelemetryConfig } from "./telemetry-config"
import { stripUrlDetails } from "./telemetry-url"

type PostHogLoader = () => Promise<PostHog>

const pages = new Set([
  "agents",
  "review",
  "usage",
  "workspaces",
  "integrations",
  "admin",
  "assistant",
  "incidents",
  "cloud-agents",
  "feature-flags",
])

/**
 * PostHog in the browser, so dashboard visits group into sessions. Events
 * carry the page title and templated paths, never ids, query strings or page content.
 */
class ProductAnalytics {
  #client: PostHog | undefined
  #environment = ""
  #platform: "web" | "desktop" = "web"
  #desktopVersion: string | undefined
  #person: SessionUser | undefined

  async initialize(
    load: PostHogLoader = async () => (await import("posthog-js")).default
  ): Promise<void> {
    let config: TelemetryConfig
    try {
      config = await TelemetryConfig.load()
    } catch (error) {
      console.warn(
        "PostHog initialization skipped: telemetry configuration unavailable",
        error
      )
      return
    }
    if (!config.posthog) return
    const client = await load().catch((error: unknown) => {
      console.warn("PostHog failed to load", error)
      return undefined
    })
    if (!client) return

    this.#environment = config.environment
    const desktop = window.openSweDesktop
    this.#platform = desktop ? "desktop" : "web"
    this.#desktopVersion = await desktop
      ?.getVersion()
      .catch((error: unknown) => {
        console.warn("Desktop version unavailable for analytics", error)
        return undefined
      })
    client.init(config.posthog.key, {
      api_host: config.posthog.host,
      defaults: "2026-08-30",
      // Behaves the same on the web and on the desktop app's custom scheme.
      persistence: "localStorage",
      person_profiles: "identified_only",
      autocapture: false,
      rageclick: false,
      capture_dead_clicks: false,
      enable_heatmaps: false,
      disable_session_recording: true,
      disable_surveys: true,
      // Exception messages and web-vitals attribution nest page content and URLs before_send cannot scrub.
      capture_exceptions: false,
      capture_performance: false,
      before_send: (event) => event && this.#redact(event),
    })
    this.#client = client
    if (this.#person) this.identify(this.#person)
  }

  /** Joins the browser's events to the person server-side events name by `users.id`. */
  identify(person: SessionUser): void {
    this.#person = person
    if (!this.#client || !person.user_id) return
    this.#client.identify(person.user_id, {
      ...(person.email ? { email: person.email } : {}),
      ...(person.github_login ? { github_login: person.github_login } : {}),
    })
  }

  reset(): void {
    this.#person = undefined
    this.#client?.reset()
  }

  #redact(event: CaptureResult): CaptureResult {
    const origin = `${window.location.protocol}//${window.location.host}`
    for (const properties of [event.properties, event.$set, event.$set_once]) {
      if (properties) this.#redactProperties(properties, origin)
    }
    Object.assign(event.properties, {
      product: "open-swe",
      environment: this.#environment,
      surface: "dashboard",
      platform: this.#platform,
      ...(this.#desktopVersion
        ? { desktop_version: this.#desktopVersion }
        : {}),
      page_name: this.#pageName(),
      $ip: "0.0.0.0",
      $geoip_disable: true,
    })
    return event
  }

  #redactProperties(properties: Properties, origin: string): void {
    for (const [key, value] of Object.entries(properties)) {
      if (typeof value === "string" && /(url|pathname|referrer)$/i.test(key))
        properties[key] = stripUrlDetails(value, origin)
    }
  }

  #pageName(): string {
    const base = import.meta.env.BASE_URL.replace(/\/$/, "")
    const { pathname } = window.location
    const section =
      (pathname.startsWith(base)
        ? pathname.slice(base.length)
        : pathname
      ).split("/")[1] || "agents"
    if (section === "my-settings") return "settings"
    return pages.has(section) ? section : "other"
  }
}

export const productAnalytics = new ProductAnalytics()
