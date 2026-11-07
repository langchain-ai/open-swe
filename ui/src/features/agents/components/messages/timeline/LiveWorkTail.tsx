import { BrainIcon } from "@phosphor-icons/react/dist/ssr/Brain"
import { useState } from "react"

import { WorkTail } from "../renderItems"
import { WorkEntryLine } from "./WorkEntryRow"
import { describeWorkEntry } from "./workEntry"
import type { WorkTailEntry } from "../renderItems"
import { cn } from "@/lib/utils"

/** One text line (`leading-5`); every tail entry is a whole number of them. */
const LINE_HEIGHT_REM = 1.25

/**
 * A folded live run's latest thoughts and tool calls, never taller than
 * `WorkTail.MAX_LINES`. Each new entry grows in at the bottom and pushes the
 * oldest up under a fade at the top edge; the fold row opens the whole log.
 */
export function LiveWorkTail({
  tail,
  repoPath,
}: {
  tail: WorkTail
  repoPath?: string
}) {
  const shown = tail.previous ? [tail.previous, ...tail.entries] : tail.entries
  // Entries already there when the tail mounts (a reload mid-run, or folding
  // the log again) show at once; only entries that arrive afterwards animate.
  const [settledKeys] = useState(() => new Set(shown.map((entry) => entry.key)))
  if (shown.length === 0) return null

  return (
    <div
      aria-label="Recent activity"
      role="group"
      className={cn(
        "flex flex-col justify-end overflow-hidden px-0.5",
        tail.previous &&
          "[mask-image:linear-gradient(to_bottom,transparent,black_2.5rem)]"
      )}
      style={{ maxHeight: `${WorkTail.MAX_LINES * LINE_HEIGHT_REM}rem` }}
    >
      {shown.map((entry) => (
        <div
          key={entry.key}
          className={cn(
            "grid shrink-0 grid-rows-[1fr]",
            !settledKeys.has(entry.key) && "motion-safe:animate-work-tail-enter"
          )}
        >
          <div className="min-h-0 overflow-hidden">
            <TailEntry entry={entry} repoPath={repoPath} />
          </div>
        </div>
      ))}
    </div>
  )
}

function TailEntry({
  entry,
  repoPath,
}: {
  entry: WorkTailEntry
  repoPath?: string
}) {
  if (entry.kind === "tool") {
    return <WorkEntryLine entry={describeWorkEntry(entry.chunk, repoPath)} />
  }
  return (
    <div className="flex gap-space-2 select-none">
      <span className="flex size-5 shrink-0 items-center justify-center text-icon-tertiary">
        <BrainIcon size={14} weight="regular" className="block" aria-hidden />
      </span>
      <div className="min-w-0 flex-1 text-xs leading-5">
        {entry.heading && (
          <p className="truncate font-medium text-primary">{entry.heading}</p>
        )}
        {entry.body && (
          <p className="line-clamp-3 text-secondary">{entry.body}</p>
        )}
      </div>
    </div>
  )
}
