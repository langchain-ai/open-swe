import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useState } from "react"

/** A one-line "Reply…" that grows into a composer, as on GitHub. ⌘↩ sends. */
export function ReplyBox({
  reply,
  placeholder = "Reply…",
}: {
  reply: {
    isPending: boolean
    mutate: (body: string, options: { onError: () => void }) => void
  }
  placeholder?: string
}) {
  const [open, setOpen] = useState(false)
  const [body, setBody] = useState("")
  const pending = reply.isPending
  const send = () => {
    const text = body.trim()
    if (!text || pending) return
    // The reply shows in the thread at once; a failed one comes back here to retry.
    setBody("")
    setOpen(false)
    reply.mutate(text, {
      onError: () => {
        setBody(text)
        setOpen(true)
      },
    })
  }
  if (!open)
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="w-full rounded-md border border-default bg-surface-level-1 px-2.5 py-1.5 text-left text-xs text-placeholder hover:border-strong"
      >
        {placeholder}
      </button>
    )
  return (
    <div className="flex flex-col gap-space-2">
      <Textarea
        size="sm"
        aria-label="Reply"
        autoFocus
        rows={3}
        resize="vertical"
        value={body}
        onChange={setBody}
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
      />
      <div className="flex justify-end gap-space-2">
        <Button
          size="sm"
          color="secondary"
          variant="plain"
          onClick={() => setOpen(false)}
        >
          Cancel
        </Button>
        <Button size="sm" disabled={!body.trim() || pending} onClick={send}>
          Reply
        </Button>
      </div>
    </div>
  )
}
