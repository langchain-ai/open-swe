import { dashboardApiBase } from "./api-base"

export interface PostHogProject {
  key: string
  host: string
}

/** What the backend says this deployment reports telemetry as. */
export class TelemetryConfig {
  constructor(
    readonly environment: string,
    readonly posthog: PostHogProject | null
  ) {}

  static async load(): Promise<TelemetryConfig> {
    const response = await fetch(
      `${dashboardApiBase()}/dashboard/api/analytics/config`,
      { cache: "no-store", signal: AbortSignal.timeout(5000) }
    )
    if (!response.ok) {
      throw new Error(`Telemetry configuration: ${response.status}`)
    }
    const config: unknown = await response.json()
    if (
      typeof config !== "object" ||
      config === null ||
      !("environment" in config) ||
      typeof config.environment !== "string" ||
      !config.environment.trim()
    ) {
      throw new Error("Telemetry environment is missing")
    }
    const posthog = "posthog" in config ? config.posthog : null
    return new TelemetryConfig(
      config.environment,
      typeof posthog === "object" &&
        posthog !== null &&
        "key" in posthog &&
        typeof posthog.key === "string" &&
        posthog.key &&
        "host" in posthog &&
        typeof posthog.host === "string" &&
        posthog.host
        ? { key: posthog.key, host: posthog.host }
        : null
    )
  }
}
