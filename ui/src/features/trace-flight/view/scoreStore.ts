/**
 * Where runs go. The app's store posts each run to the server,
 * which replays it and keeps the score it computes; the harness keeps runs in
 * memory and scores them with the same replay.
 */
import type { Run } from "../core/replay"

export interface LeaderRow {
  readonly rank: number
  readonly name: string
  readonly score: number
  readonly you: boolean
}

export interface RunResult {
  readonly score: number
  /** Your best before this run; null on your first. */
  readonly previousBest: number | null
  readonly newBest: boolean
  /** The top five, then your own row if you're further down. */
  readonly board: readonly LeaderRow[]
}

export interface BestRun {
  readonly run: Run
  readonly score: number
}

export interface ScoreStore {
  /** Your best run on this course, replayed into the trace you chase. */
  best(seed: number): Promise<BestRun | null>
  submit(run: Run): Promise<RunResult>
}
