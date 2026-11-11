import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Kbd } from "@langchain/macaw-components/Kbd"

import type { RunResult } from "./scoreStore"

/** Shown when a run ends: the score, then whether it beat your best and the board once the server answers. */
export function ScoreCard(props: {
  readonly score: number
  readonly result: RunResult | null
  readonly onPlayAgain: () => void
  readonly onExit: () => void
}) {
  const { result } = props
  return (
    <div
      role="dialog"
      aria-label="Run over"
      className="w-[36rem] max-w-[calc(100vw-2rem)] rounded-2xl border border-subtle bg-elevated p-space-6 text-primary shadow-lg"
    >
      <div className="grid gap-space-6 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <div className="flex flex-col gap-space-2">
          <span className="text-sm text-secondary">Score</span>
          <span className="text-6xl leading-none font-medium tabular-nums">
            {props.score}
          </span>
          {result?.newBest && result.previousBest !== null && (
            <Badge color="primary" size="sm" className="self-start">
              New best
            </Badge>
          )}
          {result && result.previousBest !== null && (
            <span className="text-sm text-secondary">
              {result.newBest ? "Old best" : "Best"} {result.previousBest}
            </span>
          )}
        </div>
        <div className="flex flex-col gap-space-2">
          <span className="text-sm text-secondary">Top 5</span>
          <ol className="flex flex-col font-mono text-sm">
            {result?.board.map((row) => (
              <li
                key={`${row.rank}-${row.name}`}
                className={`flex gap-space-3 rounded-md px-space-2 py-space-1 tabular-nums ${row.you ? "bg-brand-subtle" : ""}`}
              >
                <span className="w-6 text-secondary">{row.rank}</span>
                <span className="min-w-0 flex-1 truncate">{row.name}</span>
                <span>{row.score}</span>
              </li>
            ))}
          </ol>
        </div>
      </div>
      <div className="mt-space-6 flex flex-wrap gap-space-3 border-t border-muted pt-space-4">
        <Button size="sm" variant="outlined" onClick={props.onPlayAgain}>
          <Kbd>Space</Kbd> Play again
        </Button>
        <Button
          size="sm"
          variant="outlined"
          color="secondary"
          onClick={props.onExit}
        >
          <Kbd>Esc</Kbd> Back to chat
        </Button>
      </div>
    </div>
  )
}
