import { CaretRightIcon } from "@langchain/macaw-components/icons"
import { useEffect, useRef, useState } from "react"

import { cn, formatElapsed } from "@/lib/utils"
import { ElapsedSeconds } from "./ElapsedSeconds"

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
    <div className="my-space-1">
      <button
        type="button"
        onClick={() => {
          if (!isLive) setUserExpanded((value) => !value)
        }}
        className="group/reasoning flex items-center gap-space-1 rounded-sm text-left text-xs focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none disabled:cursor-default"
        aria-expanded={expanded}
        disabled={isLive}
      >
        {isLive ? (
          <>
            <span className="shimmer-text">Thinking...</span>
            <ElapsedSeconds />
          </>
        ) : (
          <>
            <CaretRightIcon
              size={12}
              weight="bold"
              className={cn(
                "shrink-0 text-icon-tertiary transition-transform duration-fast",
                expanded && "rotate-90"
              )}
              aria-hidden
            />
            <span className="text-secondary transition-colors duration-normal group-hover/reasoning:text-primary">
              {reasoningLabel(elapsedMs)}
            </span>
          </>
        )}
      </button>
      {expanded && trimmed && (
        <div className="ms-space-1 mt-space-1 border-s border-subtle ps-space-3 text-xs leading-5 break-words whitespace-pre-wrap text-secondary">
          {trimmed}
        </div>
      )}
    </div>
  )
}
