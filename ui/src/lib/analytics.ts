import type { Analytics, Context, Plugin } from "@segment/analytics-next"

import type { SessionUser } from "./api"
import { posthogAnalytics } from "./posthog"
import { TelemetryConfig } from "./telemetry-config"

type ProductEvents = {
  agents_thread_created: {
    run_target: "local" | "cloud"
    has_attachments: boolean
  }
  agents_message_submitted: {
    mode: "new_turn" | "queue" | "steer"
    has_attachments: boolean
  }
}

type SegmentClient = Pick<
  Analytics,
  "identify" | "page" | "track" | "reset" | "user" | "register"
>
type SegmentLoader = (key: string) => Promise<SegmentClient>
type PendingEvent = {
  type: "page" | "track"
  name: string
  properties: Record<string, string | boolean | number>
  path: string
  userId: string | null
  timestamp: Date
}

export class ProductAnalytics {
  #client?: SegmentClient
  #initializing?: Promise<void>
  #person?: SessionUser
  #enabled = true
  #queue: PendingEvent[] = []
  #path = "/"
  #lastPathname?: string
  #environment = ""
  #platform: "web" | "desktop" = "web"
  #desktopVersion?: string

  initialize(
    load: SegmentLoader = ProductAnalytics.loadSegment
  ): Promise<void> {
    this.#initializing ??= this.#initialize(load)
    return this.#initializing
  }

  async #initialize(load: SegmentLoader): Promise<void> {
    try {
      const config = await TelemetryConfig.load()
      void posthogAnalytics
        .initialize(undefined, config)
        .catch((error: unknown) => {
          console.warn("PostHog initialization failed", error)
        })
      if (!config.segmentWriteKey) {
        this.#enabled = false
        this.#queue = []
        return
      }
      this.#environment = config.environment
      const desktop = window.openSweDesktop
      this.#platform = desktop ? "desktop" : "web"
      this.#desktopVersion = await desktop
        ?.getVersion()
        .catch((error: unknown) => {
          console.warn("Desktop version unavailable for analytics", error)
          return undefined
        })
      const client = await load(config.segmentWriteKey)
      const sanitize = this.#sanitize.bind(this)
      const privacy: Plugin = {
        name: "Open SWE privacy",
        version: "1.0.0",
        type: "enrichment",
        isLoaded: () => true,
        load: async () => {},
        identify: sanitize,
        page: sanitize,
        track: sanitize,
      }
      await client.register(privacy)
      this.#client = client
      const userId = this.#person?.user_id ?? null
      if (this.#person) this.identify(this.#person)
      else if (this.#lastPathname !== undefined) client.reset()
      for (const event of this.#queue.splice(0)) {
        if (event.userId === userId) this.#send(event)
      }
    } catch (error) {
      this.#enabled = false
      this.#queue = []
      console.warn("Product analytics initialization skipped", error)
    }
  }

  private static async loadSegment(key: string): Promise<SegmentClient> {
    const { AnalyticsBrowser } = await import("@segment/analytics-next")
    const [client] = await AnalyticsBrowser.load(
      {
        writeKey: key,
        cdnSettings: {
          integrations: { "Segment.io": { apiKey: key } },
          metrics: { sampleRate: 0 },
        },
      },
      {
        initialPageview: false,
        useQueryString: false,
        storage: { stores: ["localStorage", "memory"] },
        integrations: {
          "Segment.io": {
            deliveryStrategy: {
              strategy: "batching",
              config: { size: 10, timeout: 5000 },
            },
          },
        },
      }
    )
    return client
  }

  identify(person: SessionUser): void {
    if (this.#person?.user_id !== person.user_id) {
      if (this.#person) this.reset()
      this.#queue = this.#queue.filter(
        (event) => event.userId === (person.user_id ?? null)
      )
    }
    this.#person = person
    posthogAnalytics.identify(person)
    if (!this.#client) return
    try {
      if (!person.user_id) {
        this.#client.reset()
        return
      }
      const previousId = this.#client.user().id()
      if (previousId && previousId !== person.user_id) this.#client.reset()
      void this.#client
        .identify(
          person.user_id,
          {
            ...(person.email ? { email: person.email } : {}),
            ...(person.github_login
              ? { github_login: person.github_login }
              : {}),
          },
          { context: { page: { path: this.#path } } }
        )
        .catch((error: unknown) =>
          console.warn("Analytics identification failed", error)
        )
    } catch (error) {
      console.warn("Analytics identification failed", error)
    }
  }

  reset(): void {
    this.#person = undefined
    this.#queue = []
    this.#lastPathname = undefined
    posthogAnalytics.reset()
    try {
      this.#client?.reset()
    } catch (error) {
      console.warn("Analytics reset failed", error)
    }
  }

  page(pathname: string, route: string): void {
    if (this.#lastPathname === pathname) return
    this.#lastPathname = pathname
    this.#path = route.replace(/\$[A-Za-z_][\w]*/g, ":id")
    this.#capture("page", this.#path, {})
  }

  track<Name extends keyof ProductEvents>(
    name: Name,
    properties: ProductEvents[Name],
    actorId: string | null | undefined = this.#person?.user_id
  ): void {
    if (!actorId || actorId !== this.#person?.user_id) return
    this.#capture("track", name, properties)
  }

  #capture(
    type: PendingEvent["type"],
    name: string,
    properties: PendingEvent["properties"]
  ): void {
    if (!this.#enabled) return
    const event: PendingEvent = {
      type,
      name,
      properties,
      path: this.#path,
      userId: this.#person?.user_id ?? null,
      timestamp: new Date(),
    }
    if (this.#client) this.#send(event)
    else if (this.#queue.length < 100) this.#queue.push(event)
  }

  #send(event: PendingEvent): void {
    if (!this.#client) return
    const properties = {
      ...event.properties,
      product: "open-swe",
      environment: this.#environment,
      platform: this.#platform,
      ...(this.#desktopVersion
        ? { desktop_version: this.#desktopVersion }
        : {}),
      schema_version: 1,
    }
    const options = {
      timestamp: event.timestamp,
      context: { page: { path: event.path } },
    }
    try {
      const sent =
        event.type === "page"
          ? this.#client.page(event.name, properties, options)
          : this.#client.track(event.name, properties, options)
      void sent.catch((error: unknown) =>
        console.warn("Analytics delivery failed", error)
      )
    } catch (error) {
      console.warn("Analytics delivery failed", error)
    }
  }

  #sanitize(ctx: Context): Context {
    const event = ctx.event
    const path = event.context?.page?.path
    event.context = {
      ip: "0.0.0.0",
      page: { path: typeof path === "string" ? path : "/" },
    }
    if (event.type === "identify") {
      const traits = event.traits
      event.traits = {
        ...(typeof traits?.email === "string" ? { email: traits.email } : {}),
        ...(typeof traits?.github_login === "string"
          ? { github_login: traits.github_login }
          : {}),
      }
    }
    if (event.properties) {
      const allowed = new Set([
        "product",
        "environment",
        "platform",
        "desktop_version",
        "schema_version",
        "run_target",
        "has_attachments",
        "mode",
      ])
      event.properties = Object.fromEntries(
        Object.entries(event.properties).filter(([key]) => allowed.has(key))
      )
    }
    return ctx
  }
}

export const productAnalytics = new ProductAnalytics()
