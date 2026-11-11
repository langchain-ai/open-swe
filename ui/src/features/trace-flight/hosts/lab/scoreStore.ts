import { replay } from "../../core/replay"
import type { BestRun, LeaderRow, ScoreStore } from "../../view/scoreStore"

const TOP = 5

/** Ranks you among other players' best scores; ties go to whoever got there first. */
export function boardOf(
  others: readonly { readonly name: string; readonly score: number }[],
  you: number
): readonly LeaderRow[] {
  const rows = [
    ...others.map((o) => ({ ...o, you: false })),
    { name: "you", score: you, you: true },
  ]
    .sort((a, b) => b.score - a.score || Number(a.you) - Number(b.you))
    .map((row, i) => ({ ...row, rank: i + 1 }))
  const top = rows.slice(0, TOP)
  const mine = rows.find((row) => row.you)
  return mine && mine.rank > TOP ? [...top, mine] : top
}

/** Keeps runs in memory, one best per course; scores come from the same replay the server runs. */
export function memoryScoreStore(
  others: readonly { readonly name: string; readonly score: number }[] = []
): ScoreStore {
  const bests = new Map<number, BestRun>()
  return {
    best: async (seed) => bests.get(seed) ?? null,
    submit: async (run) => {
      const result = replay(run)
      if (typeof result === "string") throw new Error(`Run rejected: ${result}`)
      const score = result.state.score
      const previousBest = bests.get(run.seed)?.score ?? null
      const newBest = previousBest === null || score > previousBest
      if (newBest) bests.set(run.seed, { run, score })
      return {
        score,
        previousBest,
        newBest,
        board: boardOf(others, Math.max(score, previousBest ?? 0)),
      }
    },
  }
}
