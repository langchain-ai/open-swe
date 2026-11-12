import { QueryClient } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"

import { DashboardRequestError } from "@/lib/dashboard-fetch"
import { sharedReviewOptions } from "./reviewKeys"

afterEach(() => vi.useRealTimers())

it.each([
  { status: 429, retryAfter: "60", delay: 60_000 },
  { status: 429, retryAfter: "invalid", delay: 30_000 },
  { status: 429, retryAfter: "99999999999999", delay: 30_000 },
  ...[401, 403, 404].map((status) => ({
    status,
    retryAfter: "60",
    delay: 60_000,
  })),
])(
  "handles $status with Retry-After $retryAfter",
  async ({ status, retryAfter, delay }) => {
    vi.useFakeTimers()
    const client = new QueryClient()
    const read = vi
      .fn()
      .mockRejectedValueOnce(
        new DashboardRequestError(
          status,
          "Unavailable",
          undefined,
          undefined,
          retryAfter
        )
      )
      .mockResolvedValue("recovered")
    const result = client.fetchQuery({
      queryKey: ["retry", status],
      queryFn: read,
      ...sharedReviewOptions({ owner: "acme", repo: "app", number: 1 }),
    })
    const outcome = result.catch(() => "failed")
    await vi.advanceTimersByTimeAsync(delay - 1)
    expect(read).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(await outcome).toBe(status === 429 ? "recovered" : "failed")
    expect(read).toHaveBeenCalledTimes(status === 429 ? 2 : 1)
    client.clear()
  }
)
