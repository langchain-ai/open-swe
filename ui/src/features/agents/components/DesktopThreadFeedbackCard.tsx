import { ThumbsDown, ThumbsUp } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import type { DesktopThreadFeedbackRating } from "@/desktop"
import { cn } from "@/lib/utils"

const ratings: Array<{
  value: DesktopThreadFeedbackRating
  label: string
  icon: LucideIcon
  className: string
}> = [
  {
    value: "good",
    label: "Good",
    icon: ThumbsUp,
    className:
      "border-success/40 bg-success/10 text-success-foreground hover:bg-success/20 hover:text-success-foreground dark:bg-success/10",
  },
  {
    value: "bad",
    label: "Bad",
    icon: ThumbsDown,
    className:
      "border-destructive/40 bg-destructive/10 text-destructive-foreground hover:bg-destructive/20 hover:text-destructive-foreground dark:bg-destructive/10",
  },
]

export function DesktopThreadFeedbackCard({ threadId }: { threadId: string }) {
  const [showComment, setShowComment] = useState(false)
  const [comment, setComment] = useState("")
  const [pending, setPending] = useState(false)
  const [hidden, setHidden] = useState(false)
  const [confirmation, setConfirmation] = useState(false)
  const [error, setError] = useState(false)

  const submit = async (
    rating: DesktopThreadFeedbackRating,
    feedbackComment = ""
  ) => {
    if (pending) return
    setPending(true)
    setError(false)
    try {
      await window.openSweDesktop?.submitThreadFeedback({
        threadId,
        rating,
        comment: feedbackComment,
      })
      if (rating === "bad" && !feedbackComment) {
        setShowComment(true)
      } else {
        setConfirmation(true)
      }
    } catch {
      setError(true)
    }
    setPending(false)
  }

  if (hidden) return null

  if (confirmation) {
    return (
      <div
        role="status"
        className="mt-4 rounded-lg bg-card px-4 py-3 text-sm text-muted-foreground"
      >
        Thanks for your feedback.
      </div>
    )
  }

  return (
    <form
      aria-label="Thread feedback"
      className={cn(
        "mt-4 rounded-lg bg-card p-4",
        showComment
          ? "space-y-3"
          : "flex flex-wrap items-center justify-between gap-x-6 gap-y-3"
      )}
      onSubmit={(event) => {
        event.preventDefault()
        if (!comment.trim()) return
        void submit("bad", comment.trim())
      }}
    >
      <p className="text-sm font-medium">
        {showComment ? "How could Open SWE do better?" : "How did Open SWE do?"}
      </p>
      {showComment ? (
        <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">
          Comment (optional)
          <Textarea
            autoFocus
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            maxLength={3000}
            disabled={pending}
            placeholder="What could be better?"
            className="min-h-20 border-foreground/20 bg-background text-sm"
          />
        </label>
      ) : (
        <div
          className="flex flex-wrap items-center gap-2"
          role="group"
          aria-label="Rating"
        >
          {ratings.map((option) => (
            <Button
              key={option.value}
              type="button"
              variant="outline"
              size="lg"
              className={cn("px-3 text-sm", option.className)}
              disabled={pending}
              onClick={() => void submit(option.value)}
            >
              <option.icon aria-hidden="true" className="size-4" />
              {option.label}
            </Button>
          ))}
          <Button
            type="button"
            size="lg"
            variant="ghost"
            className="text-muted-foreground"
            disabled={pending}
            onClick={() => setHidden(true)}
          >
            Dismiss
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="w-full text-xs text-destructive">
          Your feedback could not be saved. Please try again.
        </p>
      )}
      {showComment && (
        <div className="flex items-center gap-2">
          <Button type="submit" size="sm" disabled={!comment.trim() || pending}>
            {pending ? "Saving…" : "Submit comment"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={pending}
            onClick={() => setConfirmation(true)}
          >
            Skip
          </Button>
        </div>
      )}
    </form>
  )
}
