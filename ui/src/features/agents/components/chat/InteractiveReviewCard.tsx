import { Button } from "@langchain/macaw-components/Button"
import { useParams } from "@tanstack/react-router"
import { useContext, useEffect, useState } from "react"
import { toast } from "sonner"
import { Markdown } from "./Markdown"
import { ReplyOptionContext } from "./ReplyCard"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import { parseInteractiveReviewCard } from "@/features/agents/lib/interactiveReviewCard"
import { api } from "@/lib/api"

/** A chunk of an interactive review shown in the dashboard, or the review's closing card. */
export function InteractiveReviewCard({
  chunk,
}: {
  chunk: ToolExecutionChunk
}) {
  const { threadId } = useParams({ strict: false })
  const onAnswer = useContext(ReplyOptionContext)
  const [state, setState] = useState<"idle" | "sending" | "seen">("idle")
  const { loadOutput, output } = chunk
  const [loaded, setLoaded] = useState<{
    load: typeof loadOutput
    text: string
  } | null>(null)

  // A long chunk arrives as the transcript's preview, so the card fetches the rest.
  useEffect(() => {
    if (!loadOutput) return
    let active = true
    void loadOutput().then(
      (text) => {
        if (active) setLoaded({ load: loadOutput, text })
      },
      () => toast.error("Could not load this part of the review")
    )
    return () => {
      active = false
    }
  }, [loadOutput])

  const card = parseInteractiveReviewCard(
    loaded && loaded.load === loadOutput ? loaded.text : output
  )
  if (!card) return null

  const next = () => {
    if (!threadId) return
    setState("seen")
    api.interactiveReviewNext(threadId, card.nextTs).catch((error: unknown) => {
      setState("idle")
      toast.error(
        error instanceof Error
          ? error.message
          : "Could not move on to the next chunk"
      )
    })
  }
  const answer = (option: string) => {
    if (!onAnswer) return
    setState("sending")
    onAnswer(option)
      .catch(() => toast.error("Could not send your answer"))
      .finally(() => setState("idle"))
  }
  return (
    <div
      className="min-w-0 rounded-md border border-subtle px-space-3 py-space-2"
      data-testid="interactive-review-card"
    >
      <Markdown content={card.text} />
      {(card.nextTs || card.options.length > 0) && (
        <div className="mt-space-2 flex flex-wrap gap-space-2">
          {card.nextTs && (
            <Button
              color="secondary"
              size="sm"
              type="button"
              disabled={!threadId || state !== "idle"}
              onClick={next}
            >
              {state === "seen" ? "✓ Seen" : "Next"}
            </Button>
          )}
          {card.options.map((option) => (
            <Button
              key={option}
              color="secondary"
              size="sm"
              type="button"
              disabled={!onAnswer || state !== "idle"}
              onClick={() => answer(option)}
            >
              {option}
            </Button>
          ))}
        </div>
      )}
    </div>
  )
}
