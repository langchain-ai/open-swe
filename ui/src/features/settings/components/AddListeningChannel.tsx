import { useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { PlusIcon } from "@langchain/macaw-components/icons"
import {
  Dialog,
  DialogContent,
  ModalProvider,
} from "@langchain/macaw-components/Dialog"
import { Input } from "@langchain/macaw-components/Input"
import { RadioCard } from "@langchain/macaw-components/RadioCard"
import { RadioGroup } from "@langchain/macaw-components/RadioGroup"
import { Text } from "@langchain/macaw-components/Text"

import { SlackChannelCombobox } from "@/components/SlackChannelCombobox"
import {
  slackChannelLabel,
  useSlackChannelDirectory,
} from "@/lib/slack-channels"
import { ChannelPromptForm } from "./ChannelPromptEditor"
import {
  CHANNEL_PREFIX_PATTERN,
  KickoffField,
  normalizeChannelPrefix,
  patternMatches,
} from "./ListeningPatternRow"

type Target = "channel" | "pattern"

const TARGETS: Array<{ id: Target; name: string }> = [
  { id: "channel", name: "One channel" },
  { id: "pattern", name: "Channels named…" },
]

export function AddListeningChannel({
  selected,
  prefixes,
  disabled,
  error,
  onSave,
  onSavePattern,
}: {
  selected: string[]
  prefixes: string[]
  disabled: boolean
  error: string | undefined
  onSave: (id: string, prompt: string, onSuccess: () => void) => void
  onSavePattern: (
    prefix: string,
    prompt: string,
    kickoff: string,
    onSuccess: () => void
  ) => void
}) {
  const [open, setOpen] = useState(false)
  const [target, setTarget] = useState<Target>("channel")
  const [channelId, setChannelId] = useState<string | null>(null)
  const [prefixDraft, setPrefixDraft] = useState("")
  const [kickoffOn, setKickoffOn] = useState(false)
  const [kickoff, setKickoff] = useState("")
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
  const prefix = normalizeChannelPrefix(prefixDraft)
  const prefixValid = CHANNEL_PREFIX_PATTERN.test(prefix)
  const duplicatePrefix = prefixValid && prefixes.includes(prefix)
  const matches =
    prefixValid && directory.data
      ? patternMatches(
          prefix,
          directory.data.channels,
          [...prefixes, prefix],
          selected
        )
      : null
  const prefixHint = duplicatePrefix
    ? "Robin already listens by this pattern. Edit it in the list instead."
    : prefixDraft && !prefixValid
      ? "Use 1–60 letters, numbers, hyphens or underscores."
      : matches === null
        ? "For example, inc- covers #inc-api and #inc-database, and channels created later."
        : matches.length === 0
          ? "No public channel matches yet. Robin joins new ones as they’re created or renamed."
          : `Covers ${matches.length} public ${matches.length === 1 ? "channel" : "channels"} now: ${matches
              .slice(0, 3)
              .map((match) => `#${match.channel.name}`)
              .join(
                ", "
              )}${matches.length > 3 ? `, and ${matches.length - 3} more` : ""}.`

  return (
    <ModalProvider>
      <Button
        color="primary"
        leftDecorator={PlusIcon}
        disabled={disabled}
        onClick={() => {
          setTarget("channel")
          setChannelId(null)
          setPrefixDraft("")
          setKickoffOn(false)
          setKickoff("")
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
          title="Start Listening"
          description="Choose one channel, or every public channel whose name starts with a prefix. Listening starts only when you save."
          showClose={false}
          className="w-[640px] max-w-[calc(100vw-2rem)]"
          childrenClassName="max-h-[85vh] overflow-y-auto"
        >
          <RadioGroup
            aria-label="Where Robin listens"
            value={target}
            disabled={disabled}
            onValueChange={(value) => {
              setTarget(value === "pattern" ? "pattern" : "channel")
              setSubmitted(false)
            }}
            className="grid grid-cols-2 gap-space-2"
          >
            {TARGETS.map((item) => (
              <RadioCard
                key={item.id}
                value={item.id}
                aria-label={item.name}
                disabled={disabled}
                className="items-center px-space-3 py-space-2"
              >
                <Text as="span" variant="sm" weight="medium">
                  {item.name}
                </Text>
              </RadioCard>
            ))}
          </RadioGroup>
          {target === "channel" ? (
            <>
              <div className="flex flex-col gap-space-2">
                <p className="text-sm font-medium text-primary">
                  Slack Channel
                </p>
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
                  Only internal channels Robin has joined can be selected.
                  Missing a channel? Invite Robin in Slack, then refresh the
                  list.
                </p>
                {directory.isError && (
                  <p role="alert" className="text-xs text-error-secondary">
                    Could not load channels from Slack. Refresh the channel list
                    to try again.
                  </p>
                )}
                {warning && (
                  <p role="status" className="text-xs text-warning-secondary">
                    {warning}
                  </p>
                )}
              </div>
              <ChannelPromptForm
                key="channel"
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
            </>
          ) : (
            <>
              <Input
                size="md"
                label="Channel Name Starts With"
                value={prefixDraft}
                onChange={(value) => {
                  setPrefixDraft(value)
                  setSubmitted(false)
                }}
                disabled={disabled}
                placeholder="inc-"
                isError={!!prefixDraft && (!prefixValid || duplicatePrefix)}
                hintText={prefixHint}
              />
              <ChannelPromptForm
                key="pattern"
                label={prefixValid ? `#${prefix}*` : "new pattern"}
                prompt=""
                disabled={disabled}
                canSave={
                  prefixValid &&
                  !duplicatePrefix &&
                  !(kickoffOn && !kickoff.trim())
                }
                mode="add"
                promptRequired={false}
                error={submitted ? error : undefined}
                onCancel={() => setOpen(false)}
                onSave={(prompt) => {
                  if (
                    !prefixValid ||
                    duplicatePrefix ||
                    (kickoffOn && !kickoff.trim())
                  )
                    return
                  setSubmitted(true)
                  onSavePattern(
                    prefix,
                    prompt,
                    kickoffOn ? kickoff.trim() : "",
                    () => setOpen(false)
                  )
                }}
              >
                <KickoffField
                  enabled={kickoffOn}
                  kickoff={kickoff}
                  disabled={disabled}
                  onEnabledChange={setKickoffOn}
                  onKickoffChange={setKickoff}
                />
              </ChannelPromptForm>
            </>
          )}
        </DialogContent>
      </Dialog>
    </ModalProvider>
  )
}
