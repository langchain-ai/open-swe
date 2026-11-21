import { useState } from "react"
import { Badge } from "@langchain/macaw-components/Badge"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { StatusBadge } from "@langchain/macaw-components/StatusBadge"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { CaretRightIcon } from "@phosphor-icons/react/dist/ssr/CaretRight"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/ssr/PencilSimple"
import { WarningIcon } from "@phosphor-icons/react/dist/ssr/Warning"
import { XCircleIcon } from "@phosphor-icons/react/dist/ssr/XCircle"

import { ConfirmDialog } from "@/components/ConfirmDialog"
import { DropdownMenuItem } from "@/components/MenuItem"
import { CHANNEL_PROMPT_TEMPLATES } from "@/features/settings/lib/channel-prompt-templates"
import type { SlackChannelOption } from "@/lib/api"
import { slackChannelHref } from "@/lib/slack-channels"
import { ChannelPromptForm } from "./ChannelPromptEditor"

function SlackMark() {
  return (
    <svg aria-hidden="true" viewBox="0 0 2447.6 2452.5" className="size-4">
      <g clipRule="evenodd" fillRule="evenodd">
        <path
          fill="#36c5f0"
          d="m897.4 0c-135.3.1-244.8 109.9-244.7 245.2-.1 135.3 109.5 245.1 244.8 245.2h244.8v-245.1c.1-135.3-109.5-245.1-244.9-245.3.1 0 .1 0 0 0m0 654h-652.6c-135.3.1-244.9 109.9-244.8 245.2-.2 135.3 109.4 245.1 244.7 245.3h652.7c135.3-.1 244.9-109.9 244.8-245.2.1-135.4-109.5-245.2-244.8-245.3z"
        />
        <path
          fill="#2eb67d"
          d="m2447.6 899.2c.1-135.3-109.5-245.1-244.8-245.2-135.3.1-244.9 109.9-244.8 245.2v245.3h244.8c135.3-.1 244.9-109.9 244.8-245.3zm-652.7 0v-654c.1-135.2-109.4-245-244.7-245.2-135.3.1-244.9 109.9-244.8 245.2v654c-.2 135.3 109.4 245.1 244.7 245.3 135.3-.1 244.9-109.9 244.8-245.3z"
        />
        <path
          fill="#ecb22e"
          d="m1550.1 2452.5c135.3-.1 244.9-109.9 244.8-245.2.1-135.3-109.5-245.1-244.8-245.2h-244.8v245.2c-.1 135.2 109.5 245 244.8 245.2zm0-654.1h652.7c135.3-.1 244.9-109.9 244.8-245.2.2-135.3-109.4-245.1-244.7-245.3h-652.7c-135.3.1-244.9 109.9-244.8 245.2-.1 135.4 109.4 245.2 244.7 245.3z"
        />
        <path
          fill="#e01e5a"
          d="m0 1553.2c-.1 135.3 109.5 245.1 244.8 245.2 135.3-.1 244.9-109.9 244.8-245.2v-245.2h-244.8c-135.3.1-244.9 109.9-244.8 245.2zm652.7 0v654c-.2 135.3 109.4 245.1 244.7 245.3 135.3-.1 244.9-109.9 244.8-245.2v-653.9c.2-135.3-109.4-245.1-244.7-245.3-135.4 0-244.9 109.8-244.8 245.1 0 0 0 .1 0 0"
        />
      </g>
    </svg>
  )
}

export function ListeningChannelRow({
  id,
  label,
  prompt,
  channel,
  attention,
  disabled,
  error,
  onSave,
  onRemove,
}: {
  id: string
  label: string
  prompt: string
  channel: SlackChannelOption | undefined
  attention: string | null
  disabled: boolean
  error: string | undefined
  onSave: (prompt: string, onSuccess: () => void) => void
  onRemove: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [removing, setRemoving] = useState(false)
  const template = CHANNEL_PROMPT_TEMPLATES.find(
    (item) => item.id !== "custom" && item.prompt === prompt
  )
  const flattened = prompt.replace(/\s+/g, " ").trim()
  const summary =
    template?.description ??
    flattened.match(/^.*?[.!?](?=\s|$)/)?.[0] ??
    flattened
  const meta = channel
    ? [
        channel.is_private ? "Private" : "Public",
        channel.num_members === null ? null : `${channel.num_members} members`,
      ]
        .filter((part): part is string => part !== null)
        .join(" · ")
    : null

  return (
    <li className="flex flex-col gap-space-2 px-space-3 py-space-3">
      <div className="flex items-start gap-space-2 sm:gap-space-3">
        <span className="grid size-6 shrink-0 place-items-center rounded-md bg-elevated sm:size-8">
          <SlackMark />
        </span>
        <div className="flex min-h-6 min-w-0 flex-1 items-center sm:min-h-8">
          <div className="flex flex-wrap items-center gap-x-space-2 gap-y-space-1">
            <a
              href={slackChannelHref(id)}
              target="_blank"
              rel="noopener noreferrer"
              className="min-w-0 truncate text-sm font-medium text-primary hover:underline"
            >
              {label}
            </a>
            <Badge
              color={template ? "primary" : "secondary"}
              size="xs"
              title={
                template
                  ? `Matches the ${template.name} template`
                  : "Instructions written for this channel"
              }
            >
              {template?.name ?? (prompt ? "Custom" : "No instructions")}
            </Badge>
            {attention && (
              <StatusBadge variant="warning">Needs attention</StatusBadge>
            )}
            {meta && <span className="text-xs text-tertiary">{meta}</span>}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-space-1">
          {!editing && (
            <Button
              size="xs"
              color="secondary"
              variant="outlined"
              aria-label={`Edit instructions for ${label}`}
              disabled={disabled}
              onClick={() => setEditing(true)}
            >
              Edit
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <IconButton
                icon={DotsThreeIcon}
                iconWeight="bold"
                label={`More actions for ${label}`}
                size="sm"
                color="secondary"
                variant="plain"
                disabled={disabled}
                tooltipProps={{ disabled: true }}
              />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-52">
              {!editing && (
                <DropdownMenuItem onSelect={() => setEditing(true)}>
                  <PencilSimpleIcon aria-hidden size={16} weight="regular" />
                  Edit instructions
                </DropdownMenuItem>
              )}
              <DropdownMenuItem asChild>
                <a
                  href={slackChannelHref(id)}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <ArrowSquareOutIcon aria-hidden size={16} weight="regular" />
                  Open in Slack
                </a>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className="text-error-secondary"
                onSelect={() => setRemoving(true)}
              >
                <XCircleIcon aria-hidden size={16} weight="regular" />
                Stop listening
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      {(!editing || attention) && (
        <div className="flex flex-col gap-space-1 sm:pl-11">
          {!editing && summary === flattened && (
            <p className="max-w-prose text-xs/relaxed text-secondary">
              {summary ||
                "Robin responds when helpful and follows the conversation."}
            </p>
          )}
          {!editing && summary !== flattened && (
            <details className="group max-w-prose text-xs/relaxed text-secondary">
              <summary className="flex cursor-pointer list-none items-center gap-space-1 hover:text-primary">
                <CaretRightIcon
                  aria-hidden
                  size={12}
                  className="shrink-0 text-tertiary transition-transform group-open:rotate-90"
                />
                <span className="min-w-0 truncate">{summary}</span>
              </summary>
              <pre className="mt-space-2 max-h-48 overflow-y-auto rounded-md bg-elevated p-space-3 font-mono text-xxs/relaxed whitespace-pre-wrap text-secondary">
                {prompt}
              </pre>
            </details>
          )}
          {attention && (
            <div className="mt-space-1">
              <Banner
                intent="neutral"
                className="max-sm:items-start max-sm:border-transparent max-sm:bg-transparent max-sm:p-0"
                icon={
                  <WarningIcon
                    aria-hidden
                    size={16}
                    className="shrink-0 text-warning-secondary"
                  />
                }
              >
                <div className="flex flex-wrap items-center justify-between gap-space-2">
                  <span className="min-w-48 flex-1 text-secondary">
                    {attention}
                  </span>
                  <Button
                    color="secondary"
                    variant="outlined"
                    size="xs"
                    disabled={disabled}
                    onClick={() => setRemoving(true)}
                  >
                    Stop listening
                  </Button>
                </div>
              </Banner>
            </div>
          )}
        </div>
      )}
      {editing && (
        <div className="sm:pl-11">
          <ChannelPromptForm
            label={label}
            prompt={prompt}
            disabled={disabled}
            error={error}
            onSave={(draft) => onSave(draft, () => setEditing(false))}
            onCancel={() => setEditing(false)}
          />
        </div>
      )}
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        destructive
        title={`Stop listening in ${label}?`}
        description="Robin will stop replying here without an @mention, and this channel’s instructions will be removed."
        confirmLabel="Stop listening"
        onConfirm={() => {
          setRemoving(false)
          onRemove()
        }}
      />
    </li>
  )
}
