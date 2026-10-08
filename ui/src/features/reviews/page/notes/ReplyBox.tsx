import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

/** A one-line "Reply…" that grows into a composer, as on GitHub. ⌘↩ sends. */
export function ReplyBox({
  onSend,
  pending,
  placeholder = "Reply…",
}: {
  onSend: (body: string) => void
  pending: boolean
  placeholder?: string
}) {
  const [open, setOpen] = useState(false)
  const [body, setBody] = useState("")
  const send = () => {
    const text = body.trim()
    if (!text || pending) return
    onSend(text)
    setBody("")
    setOpen(false)
  }
  if (!open)
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-left text-xs text-muted-foreground hover:border-ring/40"
      >
        {placeholder}
      </button>
    )
  return (
    <div className="flex flex-col gap-2">
      <Textarea
        aria-label="Reply"
        autoFocus
        rows={3}
        value={body}
        onChange={(event) => setBody(event.target.value)}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
            event.preventDefault()
            send()
          }
          if (event.key === "Escape") {
            event.stopPropagation()
            setOpen(false)
          }
        }}
        className="resize-y text-xs"
      />
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
        <Button size="sm" disabled={!body.trim() || pending} onClick={send}>
          Reply
        </Button>
      </div>
    </div>
  )
}
