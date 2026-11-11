import { describe, expect, it, vi } from "vitest"

import { ApiError, type TraceFlightResult } from "@/lib/api"

import type { Run } from "../../core/replay"
import {
  serverScoreStore,
  StaleGame,
  type TraceFlightClient,
} from "./scoreStore"

const SEASON = { seasonId: "s1", rulesVersion: 1, seed: 42 }
const RUN: Run = { seed: 42, endTick: 10, endReason: "quit", flapDeltas: [0] }
const RESULT: TraceFlightResult = {
  score: 3,
  previousBest: null,
  newBest: true,
  board: [],
}

function client(
  submit: TraceFlightClient["submitTraceFlightRun"]
): TraceFlightClient {
  return { traceFlightBest: async () => null, submitTraceFlightRun: submit }
}

describe("serverScoreStore", () => {
  it("waits out the run limit once, so a run that ends quickly still counts", async () => {
    vi.useFakeTimers()
    const submit = vi
      .fn<TraceFlightClient["submitTraceFlightRun"]>()
      .mockRejectedValueOnce(new ApiError(429, "slow down", undefined, "5"))
      .mockResolvedValueOnce(RESULT)
    const result = serverScoreStore(SEASON, client(submit)).submit(RUN)
    await vi.advanceTimersByTimeAsync(5000)
    await expect(result).resolves.toEqual(RESULT)
    expect(submit).toHaveBeenCalledTimes(2)
    vi.useRealTimers()
  })

  it("tells the game to reload when the season moved on, even on the retry", async () => {
    vi.useFakeTimers()
    const submit = vi
      .fn<TraceFlightClient["submitTraceFlightRun"]>()
      .mockRejectedValueOnce(new ApiError(503, "busy", undefined, "2"))
      .mockRejectedValueOnce(new ApiError(409, '{"reason":"stale_season"}'))
    const caught = serverScoreStore(SEASON, client(submit))
      .submit(RUN)
      .catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(2000)
    expect(await caught).toEqual(new StaleGame("stale_season"))
    vi.useRealTimers()
  })

  it("only offers your best on the season's own course", async () => {
    const best = vi.fn(async () => ({
      seed: 42,
      endTick: 10,
      endReason: "quit",
      flapDeltas: [0],
      score: 3,
    }))
    const store = serverScoreStore(SEASON, {
      traceFlightBest: best,
      submitTraceFlightRun: async () => RESULT,
    })
    expect(await store.best(7)).toBeNull()
    expect(await store.best(42)).toEqual({ run: RUN, score: 3 })
  })
})
