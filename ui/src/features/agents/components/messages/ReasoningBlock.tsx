import { useEffect, useRef, useState } from "react"

import { Box } from "@langchain/gtm-platform-design-system/ui/box"
import {
  Collapsible,
  CollapsibleChevron,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@langchain/gtm-platform-design-system/ui/collapsible"
import { formatElapsed } from "@/lib/utils"

function reasoningLabel(elapsedMs: number | null): string {
  if (elapsedMs === null) return "Thought"
  if (elapsedMs < 1000) return "Thought briefly"
  return `Thought for ${formatElapsed(elapsedMs)}`
}

/**
 * Renders a model's reasoning ("thinking") tokens. While the reasoning is live
 * it streams in muted text under a shimmering "Thinking…" line; once the
 * reasoning ends it collapses into a "Thought for …" disclosure the user can
 * open on demand.
 */
export function ReasoningBlock({
  text,
  isLive,
}: {
  text: string
  isLive: boolean
}) {
  const [userExpanded, setUserExpanded] = useState(false)
  const [elapsedMs, setElapsedMs] = useState<number | null>(null)
  const startedAtRef = useRef<number | null>(null)
  const wasLiveRef = useRef(false)

  useEffect(() => {
    if (isLive) {
      if (startedAtRef.current === null) startedAtRef.current = Date.now()
      wasLiveRef.current = true
      return
    }
    if (wasLiveRef.current && startedAtRef.current !== null) {
      setElapsedMs(Date.now() - startedAtRef.current)
      wasLiveRef.current = false
    }
  }, [isLive])

  const trimmed = text.trim()
  if (!trimmed && !isLive) return null

  const expanded = isLive || userExpanded

  return (
    <Collapsible
      open={expanded}
      onOpenChange={(open) => {
        if (!isLive) setUserExpanded(open)
      }}
    >
      <CollapsibleTrigger
        disabled={isLive}
        className="min-h-5 cursor-pointer text-label text-ink-subtle hover:text-ink disabled:opacity-100"
      >
        {isLive ? (
          <span className="shimmer-text">Thinking...</span>
        ) : (
          <>
            <CollapsibleChevron />
            {reasoningLabel(elapsedMs)}
          </>
        )}
      </CollapsibleTrigger>
      {trimmed && (
        <CollapsibleContent>
          <Box className="mt-1 ml-1.5 border-l border-line pl-3 text-body wrap-anywhere whitespace-pre-wrap text-ink-subtle">
            {trimmed}
          </Box>
        </CollapsibleContent>
      )}
    </Collapsible>
  )
}
