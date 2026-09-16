import { Dialog } from "@base-ui/react/dialog"
import { ThumbsDown, ThumbsUp } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import type { DesktopThreadFeedbackRating } from "@/desktop"
import { cn } from "@/lib/utils"

const ratings: Array<{
  value: DesktopThreadFeedbackRating
  label: string
  icon: typeof ThumbsUp
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

export function DesktopThreadFeedbackDialog({
  open,
  onOpenChange,
  threadId,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  threadId: string
}) {
  const [rating, setRating] = useState<DesktopThreadFeedbackRating | null>(null)
  const [comment, setComment] = useState("")
  const [pending, setPending] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(false)

  const close = (nextOpen: boolean) => {
    onOpenChange(nextOpen)
    if (!nextOpen) {
      setRating(null)
      setComment("")
      setSaved(false)
      setError(false)
    }
  }

  const submit = async () => {
    if (!rating || pending) return
    setPending(true)
    setError(false)
    try {
      await window.openSweDesktop?.submitThreadFeedback({
        threadId,
        rating,
        comment: comment.trim(),
      })
      setPending(false)
      setSaved(true)
    } catch {
      setPending(false)
      setError(true)
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={close}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/50 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg bg-popover p-6 text-popover-foreground shadow-md ring-1 ring-foreground/10 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
          <Dialog.Title className="text-sm font-medium">
            Give feedback
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-xs text-muted-foreground">
            How did Open SWE do on this thread?
          </Dialog.Description>
          {saved ? (
            <div className="mt-4 flex flex-col items-start gap-3">
              <p role="status" className="text-sm text-muted-foreground">
                Thanks for your feedback.
              </p>
              <Button size="sm" onClick={() => close(false)}>
                Done
              </Button>
            </div>
          ) : (
            <div className="mt-4 space-y-4">
              <div className="flex gap-2" role="group" aria-label="Rating">
                {ratings.map((option) => (
                  <Button
                    key={option.value}
                    type="button"
                    variant="outline"
                    size="lg"
                    className={cn(
                      "px-3 text-sm",
                      option.className,
                      rating === option.value && "ring-2 ring-ring"
                    )}
                    disabled={pending}
                    onClick={() => setRating(option.value)}
                  >
                    <option.icon aria-hidden="true" className="size-4" />
                    {option.label}
                  </Button>
                ))}
              </div>
              {rating === "bad" && (
                <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">
                  How could Open SWE do better? (optional)
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
              )}
              {error && (
                <p role="alert" className="text-xs text-destructive">
                  Your feedback could not be saved. Please try again.
                </p>
              )}
              <div className="flex justify-end gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  disabled={pending}
                  onClick={() => close(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="button"
                  disabled={!rating || pending}
                  onClick={() => void submit()}
                >
                  {pending ? "Saving…" : "Submit feedback"}
                </Button>
              </div>
            </div>
          )}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
