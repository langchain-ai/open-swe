import { Button } from "@langchain/macaw-components/Button"

import { Markdown } from "@/features/agents/components/chat/Markdown"
import { ElapsedSeconds } from "./ElapsedSeconds"
import { WorkEntryRow } from "./timeline/WorkEntryRow"
import { Thought } from "./thought"

/**
 * Renders a model's reasoning ("thinking") tokens. While the reasoning is live
 * it streams in muted gray text under a shimmering header naming the current
 * thought ("Thinking…" until the model gives one); once the
 * reasoning ends it auto-collapses into a brain-icon toggle that previews the
 * reasoning and that the user can expand on demand, in the same row as tool calls.
 */
export function ReasoningBlock({
  text,
  isLive,
}: {
  text: string
  isLive: boolean
}) {
  const thought = new Thought(text)
  if (!thought.text && !isLive) return null

  const liveLabel = thought.liveLabel
    ? `${thought.liveLabel}...`
    : "Thinking..."
  const label = thought.preview || "Reasoning"
  const body = isLive ? thought.liveBody : thought.bodyUnder(thought.preview)

  if (!isLive) {
    return (
      <WorkEntryRow
        entry={{
          icon: "brain",
          heading: "Thought",
          preview: label,
          tone: "tool",
          status: "completed",
          expandedText: null,
          loadExpandedText: null,
        }}
        body={
          body ? (
            <Markdown
              content={body}
              className="text-xs leading-5 text-secondary"
            />
          ) : undefined
        }
      />
    )
  }

  return (
    <div className="my-space-1">
      <Button
        color="secondary"
        variant="plain"
        size="sm"
        aria-label={liveLabel}
        type="button"
        className="h-auto max-w-full justify-start gap-space-1 border-0 px-0 text-left text-inherit disabled:cursor-default disabled:bg-transparent"
        aria-expanded
        disabled
      >
        <span className="shimmer-text">{liveLabel}</span>
        <ElapsedSeconds />
      </Button>
      {body && (
        <div className="ms-space-1 mt-space-1 border-s border-subtle ps-space-3">
          <Markdown
            content={body}
            isLive
            className="text-xs leading-5 text-secondary"
          />
        </div>
      )}
    </div>
  )
}
