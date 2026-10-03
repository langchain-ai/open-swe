import { useEffect, useRef, useState } from "react"
import { ChevronRight } from "lucide-react"

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { formatElapsed } from "@/lib/utils"

function reasoningLabel(elapsedMs: number | null): string {
  if (elapsedMs === null) return "Thought"
  if (elapsedMs < 1000) return "Thought briefly"
  return `Thought for ${formatElapsed(elapsedMs)}`
}

/**
 * Renders a model's reasoning ("thinking") tokens. While the reasoning is live
 * it streams in muted gray text under a shimmering "Thinking…" header; once the
 * reasoning ends it auto-collapses into a "Thought for …" toggle the user can
 * expand on demand.
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
      className="my-1"
      disabled={isLive}
      open={expanded}
      onOpenChange={setUserExpanded}
    >
      <CollapsibleTrigger className="flex items-center gap-1 text-left transition-opacity hover:opacity-90 disabled:cursor-default data-disabled:cursor-default">
        {isLive ? (
          <span className="shimmer-text text-[13px]">Thinking...</span>
        ) : (
          <>
            <ChevronRight
              className={`size-3 shrink-0 text-muted-foreground/65 transition-transform ${expanded ? "rotate-90" : ""}`}
              aria-hidden
            />
            <span className="text-[13px] text-muted-foreground">
              {reasoningLabel(elapsedMs)}
            </span>
          </>
        )}
      </CollapsibleTrigger>
      {trimmed && (
        <CollapsibleContent className="ms-1 mt-1 border-s border-border/45 ps-3 text-[13px] leading-5 break-words whitespace-pre-wrap text-muted-foreground">
          {trimmed}
        </CollapsibleContent>
      )}
    </Collapsible>
  )
}
