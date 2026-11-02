import type { RumInitConfiguration } from "@datadog/browser-rum"

import { TelemetryConfig } from "./telemetry-config"
import { stripUrlDetails } from "./telemetry-url"

type PublicEnv = Record<string, string | boolean | undefined>
type RumClient = {
  init: (configuration: RumInitConfiguration) => void
  getInternalContext: () => { session_id?: string } | undefined
  addDurationVital?: (
    name: string,
    options: {
      /** Epoch milliseconds. */
      startTime: number
      duration: number
      context?: Record<string, string | number | boolean | null>
    }
  ) => void
  setGlobalContextProperty?: (key: string, value: string) => void
  addError?: (
    error: unknown,
    context?: Record<string, string | number | null>
  ) => void
}
type RumLoader = () => Promise<RumClient>

let initializedRum: RumClient | undefined
let initializedSite = "us5.datadoghq.com"
const initializationListeners = new Set<() => void>()

function envString(env: PublicEnv, name: string): string | undefined {
  const value = env[name]
  return typeof value === "string" && value.trim() ? value.trim() : undefined
}

function sampleRate(env: PublicEnv, name: string, fallback: number): number {
  const value = Number(envString(env, name))
  return Number.isFinite(value) && value >= 0 && value <= 100 ? value : fallback
}

function replaySampleRate(env: PublicEnv): number {
  const configured = env.VITE_DATADOG_SESSION_REPLAY_SAMPLE_RATE
  if (typeof configured !== "string") return 100
  const value = configured.trim() ? Number(configured) : Number.NaN
  return Number.isFinite(value) && value >= 0 && value <= 100 ? value : 0
}

function datadogAppOrigin(site: string): string {
  if (site === "datadoghq.com") return "https://app.datadoghq.com"
  if (site === "datadoghq.eu") return "https://app.datadoghq.eu"
  if (site === "ddog-gov.com") return "https://app.ddog-gov.com"
  return `https://${site}`
}

export function getDatadogSessionLink(): string | undefined {
  const sessionId = initializedRum?.getInternalContext()?.session_id
  if (!sessionId) return undefined
  const query = encodeURIComponent(`@session.id:${sessionId}`)
  return `${datadogAppOrigin(initializedSite)}/rum/explorer?query=${query}&tab=session`
}

export function isDatadogRumInitialized(): boolean {
  return initializedRum !== undefined
}

export function getDatadogRum(): RumClient | undefined {
  return initializedRum
}

export function subscribeToDatadogInitialization(
  listener: () => void
): () => void {
  initializationListeners.add(listener)
  return () => initializationListeners.delete(listener)
}

function stripEventUrlDetails(
  value: unknown,
  dashboardOrigin?: string,
  seen = new WeakSet<object>()
): void {
  if (!value || typeof value !== "object" || seen.has(value)) return
  seen.add(value)

  const record = value as Record<string, unknown>
  for (const [key, child] of Object.entries(record)) {
    if (
      typeof child === "string" &&
      (key === "url" ||
        key === "referrer" ||
        key === "resource_url" ||
        key === "source_url")
    ) {
      record[key] = stripUrlDetails(child, dashboardOrigin)
    } else {
      stripEventUrlDetails(child, dashboardOrigin, seen)
    }
  }
}

function sanitizeEventUrls(
  event: Parameters<NonNullable<RumInitConfiguration["beforeSend"]>>[0]
): boolean {
  let dashboardOrigin: string | undefined
  const viewUrl = (event as { view?: { url?: unknown } }).view?.url
  if (typeof viewUrl === "string" && URL.canParse(viewUrl))
    dashboardOrigin = new URL(viewUrl).origin
  dashboardOrigin ??=
    typeof window === "undefined" ? undefined : window.location.origin
  stripEventUrlDetails(event, dashboardOrigin)
  return true
}

export async function initializeDatadogRum(
  env: PublicEnv = import.meta.env,
  loadRum: RumLoader = async () =>
    (await import("@datadog/browser-rum")).datadogRum
): Promise<void> {
  const applicationId = envString(env, "VITE_DATADOG_APPLICATION_ID")
  const clientToken = envString(env, "VITE_DATADOG_CLIENT_TOKEN")
  if (!applicationId || !clientToken) return

  const version = envString(env, "VITE_DATADOG_VERSION")
  let environment: string
  try {
    environment = (await TelemetryConfig.load()).environment
  } catch (error) {
    console.warn(
      "Datadog initialization skipped: telemetry configuration unavailable",
      error
    )
    return
  }
  const rum = await loadRum().catch(() => undefined)
  if (!rum) return

  const site = envString(env, "VITE_DATADOG_SITE") ?? "us5.datadoghq.com"

  if (typeof window !== "undefined") {
    const globalRum = window as Window & { DD_RUM?: RumClient }
    if (globalRum.DD_RUM === rum) delete globalRum.DD_RUM
  }

  rum.init({
    applicationId,
    clientToken,
    site: site as RumInitConfiguration["site"],
    service: envString(env, "VITE_DATADOG_SERVICE") ?? "open-swe-dashboard",
    env: environment,
    ...(version ? { version } : {}),
    sessionSampleRate: sampleRate(env, "VITE_DATADOG_SESSION_SAMPLE_RATE", 100),
    sessionReplaySampleRate: replaySampleRate(env),
    trackUserInteractions: true,
    trackResources: true,
    trackLongTasks: true,
    beforeSend: sanitizeEventUrls,
    defaultPrivacyLevel: "mask",
    enablePrivacyForActionName: true,
  })
  initializedRum = rum
  initializedSite = site
  initializationListeners.forEach((listener) => listener())
}
