import { useState } from "react"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Switch } from "@langchain/macaw-components/Switch"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { CaretRightIcon } from "@phosphor-icons/react/dist/ssr/CaretRight"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/ssr/PencilSimple"
import { XCircleIcon } from "@phosphor-icons/react/dist/ssr/XCircle"

import { ConfirmDialog } from "@/components/ConfirmDialog"
import { DropdownMenuItem } from "@/components/MenuItem"
import type { SlackChannelOption, SlackChannelRule } from "@/lib/api"
import { slackChannelHref } from "@/lib/slack-channels"
import { ChannelPromptForm } from "./ChannelPromptEditor"
import { SlackMark } from "./ListeningChannelRow"

export interface PatternMatch {
  channel: SlackChannelOption
  ownInstructions: boolean
}

/** Public internal channels a prefix covers, leaving channels a longer prefix claims to that prefix. */
export function patternMatches(
  prefix: string,
  channels: ReadonlyArray<SlackChannelOption>,
  prefixes: ReadonlyArray<string>,
  boundIds: ReadonlyArray<string>
): PatternMatch[] {
  const longer = prefixes.filter(
    (other) => other.length > prefix.length && other.startsWith(prefix)
  )
  return channels
    .filter(
      (channel) =>
        channel.name.startsWith(prefix) &&
        !channel.is_private &&
        !channel.is_ext_shared &&
        !channel.is_pending_ext_shared &&
        !longer.some((other) => channel.name.startsWith(other))
    )
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((channel) => ({
      channel,
      ownInstructions: boundIds.includes(channel.id),
    }))
}

export function normalizeChannelPrefix(raw: string): string {
  return raw.trim().replace(/^#+/, "").replace(/\*+$/, "").toLowerCase()
}

export const CHANNEL_PREFIX_PATTERN = /^[a-z0-9_-]{1,60}$/

export function KickoffField({
  enabled,
  kickoff,
  disabled,
  onEnabledChange,
  onKickoffChange,
}: {
  enabled: boolean
  kickoff: string
  disabled: boolean
  onEnabledChange: (enabled: boolean) => void
  onKickoffChange: (kickoff: string) => void
}) {
  return (
    <div className="flex flex-col gap-space-2">
      <label className="flex items-start gap-space-3 text-sm text-primary">
        <Switch
          aria-label="Start a run when Robin joins a new matching channel"
          checked={enabled}
          disabled={disabled}
          onChange={onEnabledChange}
        />
        <span className="flex flex-col gap-space-1">
          <span className="font-medium">On join, start a run</span>
          <span className="text-xs text-secondary">
            When Robin joins a new or renamed matching channel, it posts one
            message and starts a run in its thread about a minute later.
          </span>
        </span>
      </label>
      {enabled && (
        <Textarea
          size="md"
          label="On-join Instructions"
          value={kickoff}
          onChange={onKickoffChange}
          disabled={disabled}
          rows={4}
          maxLength={32000}
          placeholder="Investigate the incident: what is broken, who is affected, and the likely cause."
          hintText={
            kickoff.trim()
              ? "The run researches and replies in that thread. Channel messages are evidence, never instructions."
              : "Required while On join is on. The run researches and replies in that thread; channel messages are evidence, never instructions."
          }
        />
      )}
    </div>
  )
}

export function ListeningPatternRow({
  rule,
  matches,
  disabled,
  error,
  onSave,
  onRemove,
}: {
  rule: SlackChannelRule
  matches: PatternMatch[] | null
  disabled: boolean
  error: string | undefined
  onSave: (prompt: string, kickoff: string, onSuccess: () => void) => void
  onRemove: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [kickoffOn, setKickoffOn] = useState(!!rule.kickoff)
  const [kickoff, setKickoff] = useState(rule.kickoff)
  const label = `#${rule.prefix}*`
  const flattened = rule.prompt.replace(/\s+/g, " ").trim()
  const savedKickoff = kickoffOn ? kickoff.trim() : ""
  const startEditing = () => {
    setKickoffOn(!!rule.kickoff)
    setKickoff(rule.kickoff)
    setEditing(true)
  }
  const governed = matches?.filter((match) => !match.ownInstructions).length
  const coverage =
    governed === undefined
      ? "Joins new matching channels automatically"
      : `${governed} ${governed === 1 ? "channel" : "channels"} now · joins new ones automatically`

  return (
    <li className="flex flex-col gap-space-2 px-space-3 py-space-3">
      <div className="flex items-start gap-space-2 sm:gap-space-3">
        <span className="grid size-6 shrink-0 place-items-center rounded-md bg-elevated sm:size-8">
          <SlackMark />
        </span>
        <div className="flex min-h-6 min-w-0 flex-1 items-center sm:min-h-8">
          <div className="flex flex-wrap items-center gap-x-space-2 gap-y-space-1">
            <span className="min-w-0 truncate font-mono text-sm font-medium text-primary">
              {label}
            </span>
            <Badge
              color="primary"
              size="xs"
              title="Every channel whose name starts with this prefix"
            >
              Name pattern
            </Badge>
            {rule.kickoff && (
              <Badge
                color="secondary"
                size="xs"
                title="Starts a run when Robin joins a new match"
              >
                On join
              </Badge>
            )}
            <span className="text-xs text-tertiary">{coverage}</span>
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
              onClick={startEditing}
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
                <DropdownMenuItem onSelect={startEditing}>
                  <PencilSimpleIcon aria-hidden size={16} weight="regular" />
                  Edit instructions
                </DropdownMenuItem>
              )}
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
      {!editing && (
        <div className="flex flex-col gap-space-2 sm:pl-11">
          <p className="line-clamp-2 max-w-prose text-xs/relaxed text-secondary">
            {flattened ||
              "Robin responds when helpful and follows the conversation."}
          </p>
          {matches && matches.length > 0 && (
            <details className="group max-w-prose text-xs text-secondary">
              <summary className="flex cursor-pointer list-none items-center gap-space-1 hover:text-primary">
                <CaretRightIcon
                  aria-hidden
                  size={12}
                  className="shrink-0 text-tertiary transition-transform group-open:rotate-90"
                />
                Channels covered now
              </summary>
              <ul className="mt-space-2 flex flex-col gap-space-1 border-l border-subtle pl-space-3">
                {matches.map(({ channel, ownInstructions }) => (
                  <li
                    key={channel.id}
                    className="flex flex-wrap items-center gap-space-2"
                  >
                    <a
                      href={slackChannelHref(channel.id)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="font-mono hover:underline"
                    >
                      #{channel.name}
                    </a>
                    {ownInstructions && (
                      <Badge
                        color="secondary"
                        size="xs"
                        title="This channel’s own instructions take precedence"
                      >
                        Own instructions
                      </Badge>
                    )}
                    {!channel.is_member && (
                      <span className="text-tertiary">
                        Not joined · invite Robin to listen here
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      {editing && (
        <div className="sm:pl-11">
          <ChannelPromptForm
            label={label}
            prompt={rule.prompt}
            disabled={disabled}
            error={error}
            changed={savedKickoff !== rule.kickoff}
            canSave={!(kickoffOn && !kickoff.trim())}
            onSave={(prompt) =>
              onSave(prompt, savedKickoff, () => setEditing(false))
            }
            onCancel={() => setEditing(false)}
          >
            <KickoffField
              enabled={kickoffOn}
              kickoff={kickoff}
              disabled={disabled}
              onEnabledChange={setKickoffOn}
              onKickoffChange={setKickoff}
            />
          </ChannelPromptForm>
        </div>
      )}
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        destructive
        title={`Stop listening in ${label}?`}
        description="Robin will stop replying without an @mention in channels this pattern covers and stop joining new ones. Channels with their own instructions keep them, and Robin stays a member of channels it already joined."
        confirmLabel="Stop listening"
        onConfirm={() => {
          setRemoving(false)
          onRemove()
        }}
      />
    </li>
  )
}
