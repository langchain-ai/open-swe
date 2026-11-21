import { useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { PlusIcon } from "@langchain/macaw-components/icons"
import {
  Dialog,
  DialogContent,
  ModalProvider,
} from "@langchain/macaw-components/Dialog"

import { SlackChannelCombobox } from "@/components/SlackChannelCombobox"
import {
  slackChannelLabel,
  useSlackChannelDirectory,
} from "@/lib/slack-channels"
import { ChannelPromptForm } from "./ChannelPromptEditor"

export function AddListeningChannel({
  selected,
  disabled,
  error,
  onSave,
}: {
  selected: string[]
  disabled: boolean
  error: string | undefined
  onSave: (id: string, prompt: string, onSuccess: () => void) => void
}) {
  const [open, setOpen] = useState(false)
  const [channelId, setChannelId] = useState<string | null>(null)
  const [submitted, setSubmitted] = useState(false)
  const directory = useSlackChannelDirectory(true)
  const channel = directory.data?.channels.find((row) => row.id === channelId)
  const eligible = !!channel && channel.is_member && !channel.is_ext_shared
  const alreadyListening = !!channelId && selected.includes(channelId)
  const warning =
    alreadyListening && !disabled
      ? "Robin is already listening here. Edit this channel’s instructions below instead."
      : channelId && !eligible
        ? "Choose an internal channel Robin has joined. Invite Robin in Slack, then refresh the channel list."
        : null

  return (
    <ModalProvider>
      <Button
        color="primary"
        leftDecorator={PlusIcon}
        disabled={disabled}
        onClick={() => {
          setChannelId(null)
          setSubmitted(false)
          setOpen(true)
        }}
      >
        Start listening
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!disabled) setOpen(next)
        }}
      >
        <DialogContent
          title="Start Listening to a Channel"
          description="Choose a channel and tailor how Robin helps. Listening starts only when you save."
          showClose={false}
          className="w-[640px] max-w-[calc(100vw-2rem)]"
          childrenClassName="max-h-[85vh] overflow-y-auto"
        >
          <div className="flex flex-col gap-space-2">
            <p className="text-sm font-medium text-primary">Slack Channel</p>
            <SlackChannelCombobox
              aria-label="Channel to listen to"
              value={channelId}
              onValueChange={(id) => {
                setChannelId(id)
                setSubmitted(false)
              }}
              disabled={disabled}
              eligible={(row) =>
                row.is_member &&
                !row.is_ext_shared &&
                !selected.includes(row.id)
              }
            />
            <p className="text-xs text-secondary">
              Only internal channels Robin has joined can be selected. Missing a
              channel? Invite Robin in Slack, then refresh the list.
            </p>
            {directory.isError && (
              <p role="alert" className="text-xs text-error-secondary">
                Could not load channels from Slack. Refresh the channel list to
                try again.
              </p>
            )}
            {warning && (
              <p role="status" className="text-xs text-warning-secondary">
                {warning}
              </p>
            )}
          </div>
          <ChannelPromptForm
            label={
              channelId
                ? slackChannelLabel(directory.data, channelId, selected)
                : "new channel"
            }
            prompt=""
            disabled={disabled}
            canSave={eligible && !alreadyListening}
            mode="add"
            error={submitted ? error : undefined}
            onCancel={() => setOpen(false)}
            onSave={(prompt) => {
              if (!channelId || !eligible || alreadyListening) return
              setSubmitted(true)
              onSave(channelId, prompt, () => setOpen(false))
            }}
          />
        </DialogContent>
      </Dialog>
    </ModalProvider>
  )
}
