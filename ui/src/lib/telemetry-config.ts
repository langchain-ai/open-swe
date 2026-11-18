import { dashboardApiBase } from "./api-base"

export interface PostHogProject {
  key: string
  host: string
}

export interface DatadogRumConfig {
  applicationId: string
  clientToken: string
  site: string
}

/** What the backend says this deployment reports telemetry as. */
export class TelemetryConfig {
  constructor(
    readonly environment: string,
    readonly posthog: PostHogProject | null,
    readonly segmentWriteKey: string | null = null,
    readonly datadogRum: DatadogRumConfig | null = null
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
    const rum = "datadog_rum" in config ? config.datadog_rum : null
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
        : null,
      "segment_write_key" in config &&
        typeof config.segment_write_key === "string" &&
        config.segment_write_key.trim()
        ? config.segment_write_key.trim()
        : null,
      typeof rum === "object" &&
        rum !== null &&
        "application_id" in rum &&
        typeof rum.application_id === "string" &&
        rum.application_id.trim() &&
        "client_token" in rum &&
        typeof rum.client_token === "string" &&
        rum.client_token.trim() &&
        "site" in rum &&
        typeof rum.site === "string" &&
        rum.site.trim()
        ? {
            applicationId: rum.application_id.trim(),
            clientToken: rum.client_token.trim(),
            site: rum.site.trim(),
          }
        : null
    )
  }
}
