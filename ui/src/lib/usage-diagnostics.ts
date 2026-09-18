import type { AnalyticsMetadata, UsageLeaderboardPeriod } from "@/lib/api"

export type MetricAvailability =
  | { state: "numeric"; value: number }
  | { state: "no_valid_samples" }
  | { state: "unsupported_by_backend" }

/** Distinguishes a real zero from both kinds of empty. */
export function metricAvailability(
  supported: boolean,
  value: number | null
): MetricAvailability {
  if (!supported) return { state: "unsupported_by_backend" }
  if (value == null) return { state: "no_valid_samples" }
  return { state: "numeric", value }
}

export interface UsageDiagnosticsInput {
  period: UsageLeaderboardPeriod
  /** Reports the panel currently shows, from either endpoint. */
  reports: AnalyticsMetadata[]
  /** The PR report's own server-side as_of; never another report's. */
  reportServerAsOf: string | null
  /** When this browser last received the PR report, if ever. */
  reportFetchedAt: string | null
  reportRefreshError: { status: number } | null
  avgDeliverySeconds: MetricAvailability | null
}

export function buildUsageDiagnostics(
  input: UsageDiagnosticsInput
): Record<string, unknown> {
  const latest = input.reports.length
    ? input.reports.reduce((a, b) => (a.as_of > b.as_of ? a : b))
    : null
  const eventProcessing = !input.reports.length
    ? "unknown"
    : input.reports.some((report) => report.has_failed_events)
      ? "failed"
      : input.reports.some((report) => report.has_pending_events)
        ? "pending"
        : "up_to_date"
  return {
    report: "open-swe-analytics-diagnostics",
    generated_at: new Date().toISOString(),
    period: input.period,
    pr_report: {
      fetched_at: input.reportFetchedAt,
      server_as_of: input.reportServerAsOf,
      last_refresh_failed: input.reportRefreshError != null,
      last_refresh_error_status: input.reportRefreshError?.status ?? null,
    },
    event_processing: {
      status: eventProcessing,
      reporting_since: latest?.reporting_cutover_at ?? null,
      last_processed_at: latest?.last_processed_at ?? null,
      has_pending_events: input.reports.some(
        (report) => report.has_pending_events
      ),
      has_failed_events: input.reports.some(
        (report) => report.has_failed_events
      ),
    },
    metrics: {
      avg_delivery_seconds: input.avgDeliverySeconds,
    },
  }
}
