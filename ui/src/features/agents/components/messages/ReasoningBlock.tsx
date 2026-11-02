import { Button } from "@langchain/macaw-components/Button"
import { CaretRightIcon } from "@langchain/macaw-components/icons"
import { BrainIcon } from "@phosphor-icons/react/dist/ssr/Brain"
import { useEffect, useRef, useState } from "react"

import { Markdown } from "@/features/agents/components/chat/Markdown"
import { cn, formatElapsed } from "@/lib/utils"
import { ElapsedSeconds } from "./ElapsedSeconds"
import { Thought } from "./thought"

function reasoningDuration(elapsedMs: number | null): string | undefined {
  if (elapsedMs === null) return undefined
  if (elapsedMs < 1000) return "Reasoned briefly"
  return `Reasoned for ${formatElapsed(elapsedMs)}`
}

/**
 * Renders a model's reasoning ("thinking") tokens. While the reasoning is live
 * it streams in muted gray text under a shimmering header naming the current
 * thought ("Thinking…" until the model gives one); once the
 * reasoning ends it auto-collapses into a brain-icon toggle that previews the
 * reasoning and that the user can expand on demand; how long it took is the tooltip.
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

  const thought = new Thought(text)
  if (!thought.text && !isLive) return null

  const expanded = isLive || userExpanded
  const liveLabel = thought.heading ? `${thought.heading}...` : "Thinking..."
  const label = thought.preview || "Reasoning"
  const body = thought.bodyUnder(isLive ? thought.heading : thought.preview)

  return (
    <div className="my-space-1">
      <Button
        color="secondary"
        variant="plain"
        size="sm"
        aria-label={isLive ? liveLabel : label}
        title={isLive ? undefined : reasoningDuration(elapsedMs)}
        type="button"
        onClick={() => {
          if (!isLive) setUserExpanded((value) => !value)
        }}
        className="group/reasoning h-auto max-w-full justify-start gap-space-1 border-0 px-0 text-left text-inherit focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none disabled:cursor-default disabled:bg-transparent"
        aria-expanded={expanded}
        disabled={isLive}
      >
        {isLive ? (
          <>
            <span className="shimmer-text">{liveLabel}</span>
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
            <BrainIcon
              size={12}
              className="shrink-0 text-icon-tertiary"
              aria-hidden
            />
            <span className="min-w-0 truncate text-secondary transition-colors duration-normal group-hover/reasoning:text-primary">
              {label}
            </span>
          </>
        )}
      </Button>
      {expanded && body && (
        <div className="ms-space-1 mt-space-1 border-s border-subtle ps-space-3">
          <Markdown
            content={body}
            isLive={isLive}
            className="text-xs leading-5 text-secondary"
          />
        </div>
      )}
    </div>
  )
}
