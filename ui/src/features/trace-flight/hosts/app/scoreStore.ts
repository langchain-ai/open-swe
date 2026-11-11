/**
 * The app's ScoreStore: runs go to the backend, which replays them on the
 * season's course and keeps the score it computes.
 */
import { api, ApiError, type TraceFlightSeason } from "@/lib/api"

import type { EndReason, Run } from "../../core/replay"
import type { ScoreStore } from "../../view/scoreStore"

/** The calls the store makes, so tests can stand in for the server. */
export type TraceFlightClient = Pick<
  typeof api,
  "traceFlightBest" | "submitTraceFlightRun"
>

/** The server can't count runs from this client or season; the game must reload. */
export class StaleGame extends Error {
  constructor(readonly reason: "stale_client" | "stale_season") {
    super(reason)
    this.name = "StaleGame"
  }
}

const END_REASONS: ReadonlySet<string> = new Set(["death", "quit", "cap"])

function isEndReason(value: string): value is EndReason {
  return END_REASONS.has(value)
}

function staleReason(error: ApiError): StaleGame["reason"] | null {
  if (error.status !== 409) return null
  if (error.message.includes("stale_client")) return "stale_client"
  if (error.message.includes("stale_season")) return "stale_season"
  return null
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

export function serverScoreStore(
  season: TraceFlightSeason,
  client: TraceFlightClient = api
): ScoreStore {
  const send = async (run: Run) => {
    try {
      return await client.submitTraceFlightRun({
        seasonId: season.seasonId,
        rulesVersion: season.rulesVersion,
        endTick: run.endTick,
        endReason: run.endReason,
        flapDeltas: run.flapDeltas,
      })
    } catch (error) {
      const reason = error instanceof ApiError ? staleReason(error) : null
      throw reason ? new StaleGame(reason) : error
    }
  }
  return {
    best: async (seed) => {
      if (seed !== season.seed) return null
      const best = await client.traceFlightBest()
      // The server answers for its current season, which may have moved on.
      if (!best || best.seed !== seed || !isEndReason(best.endReason))
        return null
      return {
        run: {
          seed: best.seed,
          endTick: best.endTick,
          endReason: best.endReason,
          flapDeltas: best.flapDeltas,
        },
        score: best.score,
      }
    },
    submit: async (run) => {
      try {
        return await send(run)
      } catch (error) {
        if (!(error instanceof ApiError)) throw error
        // One run every few seconds, a couple of replays at a time: a run that
        // ends quickly waits its turn once instead of being lost.
        if (error.status !== 429 && error.status !== 503) throw error
        await wait(error.retryAfterMs ?? 5000)
        return await send(run)
      }
    },
  }
}
