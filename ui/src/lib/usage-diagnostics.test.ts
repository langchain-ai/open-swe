import { describe, expect, it } from "vitest"

import type { AnalyticsMetadata } from "./api"
import { buildUsageDiagnostics, metricAvailability } from "./usage-diagnostics"

const metadata: AnalyticsMetadata = {
  reporting_cutover_at: "2026-09-11T11:00:00Z",
  collection_started_at: "2026-09-11T12:00:00Z",
  last_processed_at: "2026-09-11T12:00:30Z",
  data_source: "event_projections",
  completeness: "observed_events_only",
  has_pending_events: false,
  has_failed_events: false,
  as_of: "2026-09-11T12:01:00Z",
  build_info: {
    backend: {
      revision_id: "rev-42",
      commit: "abc123",
      built_at: "2026-09-10T10:00:00Z",
      package_version: "0.1.0",
    },
    dashboard: {
      commit: "def456",
      built_at: "2026-09-10T11:00:00Z",
      served: true,
    },
  },
}

describe("metricAvailability", () => {
  it.each([
    [false, null, { state: "unsupported_by_backend" }],
    [false, 42, { state: "unsupported_by_backend" }],
    [true, null, { state: "no_valid_samples" }],
    [true, 0, { state: "numeric", value: 0 }],
    [true, 3600, { state: "numeric", value: 3600 }],
  ] as const)("supported=%s value=%s is %o", (supported, value, expected) => {
    expect(metricAvailability(supported, value)).toEqual(expected)
  })
})

describe("buildUsageDiagnostics", () => {
  it("carries only allowlisted displayed state", () => {
    const result = buildUsageDiagnostics({
      period: "30d",
      reports: [metadata, { ...metadata, as_of: "2026-09-11T12:05:00Z" }],
      reportServerAsOf: metadata.as_of,
      reportFetchedAt: "2026-09-11T12:01:30Z",
      reportRefreshError: null,
      avgDeliverySeconds: { state: "numeric", value: 0 },
    })
    expect(result).not.toHaveProperty("api")
    expect(result).not.toHaveProperty("build")
    expect(result.period).toBe("30d")
    expect(result.pr_report).toEqual({
      fetched_at: "2026-09-11T12:01:30Z",
      server_as_of: "2026-09-11T12:01:00Z",
      last_refresh_failed: false,
      last_refresh_error_status: null,
    })
    expect(result.event_processing).toEqual({
      status: "up_to_date",
      reporting_since: "2026-09-11T11:00:00Z",
      last_processed_at: "2026-09-11T12:00:30Z",
      has_pending_events: false,
      has_failed_events: false,
    })
    expect(result.metrics).toEqual({
      avg_delivery_seconds: { state: "numeric", value: 0 },
    })
    const serialized = JSON.stringify(result)
    // A fresher leaderboard report must not leak into the PR report's fields.
    expect(serialized).not.toContain("2026-09-11T12:05:00Z")
    for (const forbidden of [
      "unavailable_thread_ids",
      "thread",
      "login",
      "email",
      "token",
      "session",
      "user",
    ]) {
      expect(serialized).not.toContain(forbidden)
    }
  })

  it("marks report times unknown when the backend cannot supply them", () => {
    const result = buildUsageDiagnostics({
      period: "7d",
      reports: [],
      reportServerAsOf: null,
      reportFetchedAt: null,
      reportRefreshError: { status: 503 },
      avgDeliverySeconds: { state: "unsupported_by_backend" },
    })
    expect(result.pr_report).toMatchObject({
      fetched_at: null,
      server_as_of: null,
      last_refresh_failed: true,
      last_refresh_error_status: 503,
    })
    expect(result.event_processing).toMatchObject({
      status: "unknown",
      reporting_since: null,
      last_processed_at: null,
    })
    expect(result.metrics).toEqual({
      avg_delivery_seconds: { state: "unsupported_by_backend" },
    })
  })

  it("reports event-processing status per state", () => {
    const withState = (pending: boolean, failed: boolean) =>
      buildUsageDiagnostics({
        period: "7d",
        reports: [
          {
            ...metadata,
            has_pending_events: pending,
            has_failed_events: failed,
          },
        ],
        reportServerAsOf: metadata.as_of,
        reportFetchedAt: null,
        reportRefreshError: null,
        avgDeliverySeconds: null,
      }).event_processing as Record<string, unknown>
    expect(withState(false, false).status).toBe("up_to_date")
    expect(withState(true, false).status).toBe("pending")
    expect(withState(false, true).status).toBe("failed")
    expect(withState(true, true).status).toBe("failed")
  })
})
