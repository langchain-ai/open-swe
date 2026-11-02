import { Button } from "@langchain/macaw-components/Button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@langchain/macaw-components/Dialog"

import { RepositoryPicker } from "@/features/plugins/components/RepositoryPicker"
import type { InstalledPlugin } from "@/features/plugins/lib/api"
import type { Answer, Approval } from "@/features/plugins/lib/approval"

/**
 * What a plugin asks the person, in a dialog the web UI draws over it, so the
 * plugin can neither cover nor imitate it. Approving works only once it has
 * been on screen a moment (`armed`), so a click aimed at the plugin cannot
 * land on it.
 */
export function ApprovalDialog({
  plugin,
  approval,
  armed,
  onAnswer,
}: {
  plugin: InstalledPlugin
  approval: Approval | null
  armed: boolean
  onAnswer: (answer: Answer | null) => void
}) {
  const name = plugin.manifest.displayName
  return (
    <Dialog
      open={approval !== null}
      onOpenChange={(open) => !open && onAnswer(null)}
    >
      <DialogContent
        childrenClassName="gap-space-4 p-space-6"
        showClose={false}
      >
        {approval?.kind === "repositories" ? (
          <>
            <DialogTitle>
              Share {approval.pick.multiple ? "repositories" : "a repository"}{" "}
              with {name}?
            </DialogTitle>
            <DialogDescription>
              {name} reaches only what you share, as you and with your access.
              You can stop sharing in Settings → Plugins.
            </DialogDescription>
            <RepositoryPicker
              key={approval.pick.suggested.join(",")}
              plugin={plugin}
              pick={approval.pick}
              armed={armed}
              onAnswer={onAnswer}
            />
          </>
        ) : approval?.kind === "link" ? (
          <>
            <DialogTitle>Open this link?</DialogTitle>
            <DialogDescription>
              {name} wants to open a page outside Open SWE in a new tab.
            </DialogDescription>
            <code className="rounded-md border border-default bg-surface-level-1 p-space-2 text-xs break-all text-primary">
              {approval.url}
            </code>
            <Answers armed={armed} confirm="Open link" onAnswer={onAnswer} />
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

function Answers({
  armed,
  confirm,
  onAnswer,
}: {
  armed: boolean
  confirm: string
  onAnswer: (answer: Answer | null) => void
}) {
  return (
    <div className="flex justify-end gap-space-2">
      <Button size="sm" variant="outlined" onClick={() => onAnswer(null)}>
        Cancel
      </Button>
      <Button size="sm" disabled={!armed} onClick={() => onAnswer(true)}>
        {confirm}
      </Button>
    </div>
  )
}
