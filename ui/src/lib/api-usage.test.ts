import { afterEach, describe, expect, it, vi } from "vitest"

import { api } from "./api"

vi.mock("@tanstack/react-start/server", () => ({
  getRequestHeader: () => undefined,
}))

afterEach(() => vi.unstubAllGlobals())

describe("usageLeaderboard", () => {
  it.each([
    {
      name: "legacy backend",
      metrics: { agent_runs: 12, avg_run_seconds: 90 },
      expected: { invocations: 12, avg_invocation_seconds: 90 },
    },
    {
      name: "current backend without aliases",
      metrics: { invocations: 8, avg_invocation_seconds: 45 },
      expected: { invocations: 8, avg_invocation_seconds: 45 },
    },
    {
      name: "current backend with zero values and legacy aliases",
      metrics: {
        invocations: 0,
        avg_invocation_seconds: 0,
        agent_runs: 12,
        avg_run_seconds: 90,
      },
      expected: { invocations: 0, avg_invocation_seconds: 0 },
    },
  ])("normalizes metrics from $name", async ({ metrics, expected }) => {
    const row = {
      rank: 1,
      user: { name: "Example", github_login: "example", email: null },
      favorite_model: "test-model",
      prs_opened: 3,
      merged_prs: 2,
      agent_loc: 100,
      additions: 120,
      deletions: 20,
      total_tokens: 1000,
      total_cost_usd: 0.5,
      ...metrics,
    }
    const payload = {
      period: "30d",
      rows: [row],
      total_members: 1,
      current_user_rank: 1,
      generated_at_ms: 1000,
      reviewer_stats: null,
    }
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(payload)))

    expect(await api.usageLeaderboard()).toEqual({
      ...payload,
      rows: [{ ...row, ...expected }],
    })
  })
})
