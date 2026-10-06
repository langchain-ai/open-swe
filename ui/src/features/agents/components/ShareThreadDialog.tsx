import { useState } from "react"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Checkbox } from "@langchain/gtm-platform-design-system/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@langchain/gtm-platform-design-system/ui/dialog"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"

import { AlertTriangle, Share2 } from "@/components/glyphs"

/*
 * Not ConfirmableAction: sharing is irreversible consent rather than a delete,
 * and it is gated on an explicit acknowledgement and on no run being live,
 * neither of which that pattern carries. Its footer law is kept: Cancel stays
 * on screen and the risk confirm is an outline, never a fill.
 */
export function ShareThreadDialog({
  open,
  onOpenChange,
  busy,
  running,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  busy: boolean
  running: boolean
  onConfirm: () => void
}) {
  const [acknowledged, setAcknowledged] = useState(false)
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return
        setAcknowledged(false)
        onOpenChange(next)
      }}
      disablePointerDismissal={busy}
    >
      <DialogContent showCloseButton={!busy} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            <Inline gap="sm" align="center" ink="risk">
              <Icon icon={AlertTriangle} />
              Expose this entire thread to the workspace?
            </Inline>
          </DialogTitle>
          <DialogDescription>
            Everyone with workspace access will be able to read everything
            already in this thread and anything added later: messages, private
            tool results, attachments, plans, and sandbox files. This may
            include secrets or sensitive personal information.
          </DialogDescription>
        </DialogHeader>
        <Stack gap="md" className="text-label">
          <Box render={<p />} className="font-medium text-ink">
            This cannot be undone. Continuing privately creates a new copy; it
            does not hide this shared thread.
          </Box>
          <Box render={<p />} className="text-ink-subtle">
            Private-only tools, personal integrations, and private admin
            capabilities will no longer be available in this thread.
          </Box>
          {running && (
            <Box render={<p />} role="alert" className="text-risk">
              Stop the active run before sharing this thread.
            </Box>
          )}
          <Inline
            render={<label />}
            gap="sm"
            align="start"
            className="text-ink"
          >
            <Checkbox
              checked={acknowledged}
              onCheckedChange={setAcknowledged}
              disabled={busy}
              className="mt-px"
            />
            I understand that everything in this thread becomes visible to the
            workspace.
          </Inline>
        </Stack>
        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => {
              setAcknowledged(false)
              onOpenChange(false)
            }}
            disabled={busy}
          >
            Keep private
          </Button>
          <Button
            variant="outline"
            aria-label="Share entire thread"
            className="border-risk text-risk hover:bg-risk-bg"
            onClick={onConfirm}
            disabled={!acknowledged || running}
            loading={busy}
          >
            <Icon icon={Share2} />
            Share entire thread
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
