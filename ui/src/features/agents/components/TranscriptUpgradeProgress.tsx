import { LinearProgress } from "@langchain/macaw-components/LinearProgress"
import { ProgressBar } from "@langchain/macaw-components/ProgressBar"

import type { TranscriptUpgradeProgress as Progress } from "@/features/agents/lib/transcript/api"

/** Shown in place of the thread while its history moves to the transcript log. */
export function TranscriptUpgradeProgress({
  progress,
}: {
  progress: Progress | null
}) {
  const known = progress !== null && progress.total > 0
  return (
    <main className="flex min-w-0 flex-1 items-center justify-center p-space-5">
      <div className="flex w-full max-w-md flex-col gap-space-3">
        <div className="flex flex-col gap-space-1">
          <h2 className="text-sm font-medium text-primary">
            Speeding up this thread
          </h2>
          <p className="text-xs text-secondary">
            Moving its history to the faster transcript reader. This happens
            once per thread.
          </p>
        </div>
        {known ? (
          <ProgressBar
            aria-label="Thread upgrade progress"
            value={progress.done}
            max={progress.total}
            label={`${progress.done.toLocaleString()} of ${progress.total.toLocaleString()} events`}
            labelPosition="bottom"
          />
        ) : (
          <LinearProgress aria-label="Reading thread history" thicknessPx={4} />
        )}
      </div>
    </main>
  )
}
