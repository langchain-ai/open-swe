/**
 * Trace Flight in the app: the open season's course, the server's scores and the
 * app's theme. When the server can't count runs, the game still plays and the
 * score card says the run wasn't saved; when the rules moved on, it asks for a
 * reload instead of playing a course the server would refuse.
 */
import { useMemo, useState, useSyncExternalStore } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { useQuery } from "@tanstack/react-query"

import { ApiError, type TraceFlightSeason } from "@/lib/api"
import { productAnalytics } from "@/lib/analytics"
import { useTheme } from "@/lib/theme"

import { RULES_VERSION } from "../../core/rules"
import type { ScoreStore } from "../../view/scoreStore"
import { TraceFlight } from "../../view/TraceFlight"
import { seasonQuery } from "./season"
import { serverScoreStore, StaleGame } from "./scoreStore"

/** The course played when no season is reachable; its runs are never saved. */
const UNSAVED_SEED = 1

const UNSAVED: ScoreStore = {
  best: async () => null,
  submit: () => Promise.reject(new Error("no season to save runs to")),
}

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)"

function subscribeToMotion(onChange: () => void): () => void {
  const query = window.matchMedia(REDUCED_MOTION)
  query.addEventListener("change", onChange)
  return () => query.removeEventListener("change", onChange)
}

function rejectionReason(error: unknown): string {
  if (error instanceof StaleGame) return error.reason
  if (error instanceof ApiError) return `http_${error.status}`
  return "unreachable"
}

export function TraceFlightGame(props: {
  /** The logo the bird leaps out of and flies back to. */
  readonly home: () => DOMRectReadOnly | null
  /** The bird has left the logo; until then the logo stays in place. */
  readonly onDepart: () => void
  readonly onExit: () => void
}) {
  const season = useQuery(seasonQuery)
  const { resolvedTheme } = useTheme()
  const reducedMotion = useSyncExternalStore(
    subscribeToMotion,
    () => window.matchMedia(REDUCED_MOTION).matches,
    () => false
  )
  const [stale, setStale] = useState(false)
  // The game keeps the season it opened with: a new one mid-game would send the
  // last run's inputs as a run on the new course.
  const [pinned, setPinned] = useState<{
    readonly season: TraceFlightSeason | null
  } | null>(null)
  if (!pinned && !season.isPending) setPinned({ season: season.data ?? null })
  const current = pinned?.season ?? null
  const scoreStore = useMemo(
    () => (current ? serverScoreStore(current) : UNSAVED),
    [current]
  )

  if (!pinned) return null
  if (stale || (current && current.rulesVersion !== RULES_VERSION))
    return (
      <div className="grid size-full place-items-center bg-surface-level-1">
        <div className="flex flex-col items-center gap-space-3 text-center">
          <p className="text-secondary">Robin updated. Reload to play.</p>
          <div className="flex gap-space-2">
            <Button size="sm" onClick={() => window.location.reload()}>
              Reload
            </Button>
            <Button
              size="sm"
              variant="outlined"
              color="secondary"
              onClick={props.onExit}
            >
              Back to chat
            </Button>
          </div>
        </div>
      </div>
    )

  return (
    <TraceFlight
      seed={current ? current.seed : UNSAVED_SEED}
      scoreStore={scoreStore}
      theme={resolvedTheme}
      reducedMotion={reducedMotion}
      home={props.home}
      onDepart={props.onDepart}
      onExit={props.onExit}
      onRunEnd={(run) =>
        productAnalytics.track("trace_flight_run_finished", {
          score: run.score,
          level: run.level,
          end_reason: run.endReason,
        })
      }
      onSubmitError={(error) => {
        productAnalytics.track("trace_flight_submit_rejected", {
          reason: rejectionReason(error),
        })
        if (error instanceof StaleGame) setStale(true)
      }}
    />
  )
}
