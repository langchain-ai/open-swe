import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { Input } from "@langchain/macaw-components/Input"
import { useState } from "react"

import { useRenameAgentThread } from "@/features/agents/lib/queries"
import type { AgentThread } from "@/features/agents/lib/types"

export function RenameThreadDialog({
  thread,
  onClose,
}: {
  thread: AgentThread
  onClose: () => void
}) {
  const [draft, setDraft] = useState(thread.title)
  const renameThread = useRenameAgentThread()
  const title = draft.trim()

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent
        title="Rename thread"
        showClose={false}
        className="w-[min(28rem,calc(100vw-2rem))]"
      >
        <form
          className="flex flex-col gap-space-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (!title || title.length > 80 || renameThread.isPending) return
            if (title !== thread.title) {
              renameThread.mutate({ threadId: thread.id, title })
            }
            onClose()
          }}
        >
          <Input
            label="Thread title"
            size="md"
            value={draft}
            onChange={setDraft}
            debounceMs={0}
            maxLength={80}
            autoFocus
            onFocus={(event) => event.currentTarget.select()}
            onKeyDown={(event) => {
              if (event.key === "Enter" && event.nativeEvent.isComposing) {
                event.preventDefault()
              }
            }}
          />
          <div className="flex justify-end gap-space-2">
            <Button
              size="xs"
              color="secondary"
              variant="outlined"
              type="button"
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button
              size="xs"
              type="submit"
              disabled={!title || title.length > 80 || renameThread.isPending}
            >
              Save
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
