import { BrainIcon } from "@phosphor-icons/react/dist/ssr/Brain"

import type { WorkTail, WorkTailEntry } from "../renderItems"
import { WorkEntryRow } from "./WorkEntryRow"
import { ShellEntryBody, JsonInputEntryBody } from "./entryBodies"
import { describeWorkEntry, hasToolCallDetail, latestDiff } from "./workEntry"
import { DiffView } from "@/features/agents/components/chat/DiffView"

/** The current thought and a collapsed disclosure for the latest tool call. */
export function LiveWorkTail({
  tail,
  repoPath,
}: {
  tail: WorkTail
  repoPath?: string
}) {
  if (tail.entries.length === 0) return null

  return (
    <div aria-label="Recent activity" role="group" className="space-y-space-1">
      {tail.entries.map((entry) => (
        <TailEntry key={entry.key} entry={entry} repoPath={repoPath} />
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
    const view = describeWorkEntry(entry.chunk, repoPath)
    const diff = latestDiff(entry.chunk)
    const Body =
      entry.chunk.toolKind === "execute" ? ShellEntryBody : JsonInputEntryBody
    return (
      <WorkEntryRow
        entry={view}
        timestamp={entry.chunk.timestamp}
        body={
          diff && entry.chunk.status !== "error" ? (
            <DiffView diffData={diff} snippet />
          ) : hasToolCallDetail(
              entry.chunk,
              Object.keys(entry.chunk.input ?? {}).length > 0
            ) ? (
            ({ loadedText, loadError }) => (
              <Body
                chunk={entry.chunk}
                loadedText={loadedText}
                loadError={loadError}
              />
            )
          ) : undefined
        }
      />
    )
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
