import { ChevronRight } from "lucide-react"

import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
  useReasoning,
} from "@/components/ai-elements/reasoning"
import { formatElapsed } from "@/lib/utils"

function ReasoningLabel() {
  const { isOpen, isStreaming, duration } = useReasoning()
  if (isStreaming) {
    return <span className="shimmer-text text-[13px]">Thinking...</span>
  }
  const label =
    duration === undefined
      ? "Thought"
      : `Thought for ${formatElapsed(duration * 1000)}`
  return (
    <>
      <ChevronRight
        className={`size-3 shrink-0 text-muted-foreground/65 transition-transform ${isOpen ? "rotate-90" : ""}`}
        aria-hidden
      />
      <span className="text-[13px] text-muted-foreground">{label}</span>
    </>
  )
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
  const trimmed = text.trim()
  if (!trimmed && !isLive) return null

  return (
    <Reasoning className="my-1 mb-1" isStreaming={isLive}>
      <ReasoningTrigger className="w-auto gap-1 text-left">
        <ReasoningLabel />
      </ReasoningTrigger>
      {trimmed && (
        <ReasoningContent className="ms-1 mt-1 border-s border-border/45 ps-3 text-[13px] leading-5 break-words text-muted-foreground">
          {trimmed}
        </ReasoningContent>
      )}
    </Reasoning>
  )
}
