import type { Analytics, Context, Plugin } from "@segment/analytics-next"

import type { SessionUser } from "./api"
import { posthogAnalytics } from "./posthog"
import { TelemetryConfig } from "./telemetry-config"

const ACTIONS = [
  "start_thread",
  "new_turn",
  "queue",
  "steer",
  "offload",
  "cancel",
  "queue_cancel",
  "project_create",
  "project_update",
  "project_delete",
  "project_share",
  "project_unshare",
  "project_move_thread",
  "thread_share",
  "integration_connect",
  "integration_disconnect",
  "mcp_save",
  "mcp_delete",
  "automation_create",
  "automation_update",
  "automation_delete",
  "automation_run",
  "settings_save",
  "review_request",
  "review_submit",
  "review_rerun",
] as const
export type ProductActionName = (typeof ACTIONS)[number]
const FEATURES = [
  "terminal",
  "browser",
  "files",
  "file",
  "diff",
  "agents",
  "background",
  "app",
  "artifact",
  "review",
  "walkthrough",
  "findings",
] as const
export type ProductFeature = (typeof FEATURES)[number]
type ActionResult = "accepted" | "failed"
type FailureKind =
  | "none"
  | "network"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "validation"
  | "server"
  | "unknown"

type ProductEvents = {
  agents_action_started: { action: ProductActionName }
  agents_action_completed: {
    action: ProductActionName
    result: ActionResult
    failure_kind: FailureKind
  }
  agents_feature_opened: { feature: ProductFeature }
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
    if (
      ![
        "agents_thread_created",
        "agents_message_submitted",
        "agents_action_started",
        "agents_action_completed",
        "agents_feature_opened",
      ].includes(name)
    )
      return
    this.#capture("track", name, properties)
  }

  beginAction(
    action: ProductActionName,
    actorId: string | null | undefined = this.#person?.user_id
  ): ProductAction {
    this.track("agents_action_started", { action }, actorId)
    return new ProductAction(this, action, actorId ?? null)
  }

  featureOpened(feature: ProductFeature): void {
    this.track("agents_feature_opened", { feature })
  }

  async mutation<T>(
    path: string,
    method: string | undefined,
    send: () => Promise<T>
  ): Promise<T> {
    const action = ProductAnalytics.mutationAction(path, method)
    if (!action) return send()
    const pending = this.beginAction(action)
    try {
      const result = await send()
      pending.accepted()
      return result
    } catch (error) {
      pending.failed(error)
      throw error
    }
  }

  private static mutationAction(
    path: string,
    method = "GET"
  ): ProductActionName | undefined {
    const route = path.split("?", 1)[0] ?? ""
    const routes: ReadonlyArray<readonly [string, RegExp, ProductActionName]> =
      [
        ["PUT", /^\/me\/preferences$/, "settings_save"],
        ["POST", /^\/projects$/, "project_create"],
        ["PATCH", /^\/projects\/[^/]+$/, "project_update"],
        ["DELETE", /^\/projects\/[^/]+$/, "project_delete"],
        ["PUT", /^\/projects\/[^/]+\/members$/, "project_share"],
        ["DELETE", /^\/projects\/[^/]+\/members\/[^/]+$/, "project_unshare"],
        ["PUT", /^\/projects\/[^/]+\/threads\/[^/]+$/, "project_move_thread"],
        ["DELETE", /^\/projects\/threads\/[^/]+$/, "project_move_thread"],
        ["POST", /^\/threads\/[^/]+\/share-to-workspace$/, "thread_share"],
        ["POST", /^\/(?:admin\/)?threads\/[^/]+\/cancel$/, "cancel"],
        ["POST", /^\/schedules$/, "automation_create"],
        ["PATCH", /^\/schedules\/[^/]+$/, "automation_update"],
        ["DELETE", /^\/schedules\/[^/]+$/, "automation_delete"],
        ["POST", /^\/schedules\/[^/]+\/trigger$/, "automation_run"],
        [
          "POST",
          /^\/my-managed-tools\/[^/]+\/connect\/[^/]+$/,
          "integration_connect",
        ],
        [
          "DELETE",
          /^\/my-managed-tools\/[^/]+\/services\/[^/]+$/,
          "integration_disconnect",
        ],
        ["PUT", /^\/(?:my-mcps|mcps)\/[^/]+$/, "mcp_save"],
        ["DELETE", /^\/(?:my-mcps|mcps)\/[^/]+$/, "mcp_delete"],
        ["PUT", /^\/workspaces\/[^/]+\/mcps\/[^/]+$/, "mcp_save"],
        ["DELETE", /^\/workspaces\/[^/]+\/mcps\/[^/]+$/, "mcp_delete"],
        [
          "POST",
          /^\/repos\/[^/]+\/[^/]+\/pulls\/\d+\/human-review$/,
          "review_request",
        ],
        ["POST", /^\/reviews\/[^/]+\/[^/]+\/\d+\/re-review$/, "review_rerun"],
        [
          "POST",
          /^\/reviews\/[^/]+\/[^/]+\/\d+\/submit-review$/,
          "review_submit",
        ],
      ]
    return routes.find(
      ([verb, pattern]) => method === verb && pattern.test(route)
    )?.[2]
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

  private static allowedProperty(
    type: string,
    name: unknown,
    key: string,
    value: unknown
  ): boolean {
    if (key === "product") return value === "open-swe"
    if (key === "environment")
      return (
        typeof value === "string" &&
        [
          "",
          "local",
          "development",
          "dev",
          "test",
          "staging",
          "production",
          "prod",
        ].includes(value)
      )
    if (key === "platform") return value === "web" || value === "desktop"
    if (key === "schema_version") return value === 1
    if (key === "desktop_version")
      return (
        typeof value === "string" &&
        /^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?$/.test(value) &&
        value.length < 64
      )
    if (type !== "track") return false
    if (name === "agents_feature_opened")
      return key === "feature" && FEATURES.some((feature) => feature === value)
    if (
      name === "agents_action_started" ||
      name === "agents_action_completed"
    ) {
      if (key === "action") return ACTIONS.some((action) => action === value)
      if (name !== "agents_action_completed") return false
      if (key === "result") return value === "accepted" || value === "failed"
      return (
        key === "failure_kind" &&
        typeof value === "string" &&
        [
          "none",
          "network",
          "unauthorized",
          "forbidden",
          "not_found",
          "conflict",
          "validation",
          "server",
          "unknown",
        ].includes(value)
      )
    }
    if (name === "agents_thread_created") {
      if (key === "run_target") return value === "local" || value === "cloud"
      return key === "has_attachments" && typeof value === "boolean"
    }
    if (name === "agents_message_submitted") {
      if (key === "mode")
        return value === "new_turn" || value === "queue" || value === "steer"
      return key === "has_attachments" && typeof value === "boolean"
    }
    return false
  }

  #sanitize(ctx: Context): Context {
    const event = ctx.event
    const path = event.context?.page?.path
    const library = event.context?.library
    event.context = {
      ip: "0.0.0.0",
      ...(library?.name === "analytics.js" &&
      typeof library.version === "string" &&
      /^(?:(?:npm:)?next-)?\d+\.\d+\.\d+(?:-[a-z0-9.]+)?$/.test(
        library.version
      ) &&
      library.version.length < 64
        ? { library: { name: library.name, version: library.version } }
        : {}),
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
      event.properties = Object.fromEntries(
        Object.entries(event.properties).filter(([key, value]) =>
          ProductAnalytics.allowedProperty(event.type, event.event, key, value)
        )
      )
    }
    return ctx
  }
}

export class ProductAction {
  #settled = false
  constructor(
    private readonly analytics: ProductAnalytics,
    private readonly action: ProductActionName,
    private readonly actorId: string | null | undefined
  ) {}

  accepted(): void {
    this.finish("accepted", "none")
  }

  failed(error: unknown): void {
    const status =
      typeof error === "object" && error !== null && "status" in error
        ? error.status
        : undefined
    const failure: FailureKind =
      status === 401
        ? "unauthorized"
        : status === 403
          ? "forbidden"
          : status === 404
            ? "not_found"
            : status === 409
              ? "conflict"
              : status === 400 || status === 422
                ? "validation"
                : typeof status === "number" && status >= 500
                  ? "server"
                  : status === 0 || error instanceof TypeError
                    ? "network"
                    : "unknown"
    this.finish("failed", failure)
  }

  private finish(result: ActionResult, failure_kind: FailureKind): void {
    if (this.#settled) return
    this.#settled = true
    this.analytics.track(
      "agents_action_completed",
      { action: this.action, result, failure_kind },
      this.actorId
    )
  }
}

export const productAnalytics = new ProductAnalytics()
