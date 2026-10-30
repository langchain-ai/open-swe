import { CheckIcon, GlobeRegularIcon } from "@langchain/macaw-components/icons"
import { Button } from "@langchain/macaw-components/Button"
import { SpinnerIcon } from "@langchain/macaw-components/Spinner"
import { Typeahead } from "@langchain/macaw-components/Typeahead"
import { ArrowClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowClockwise"
import { ClockCounterClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ClockCounterClockwise"
import { HashIcon } from "@phosphor-icons/react/dist/ssr/Hash"
import { LockSimpleIcon } from "@phosphor-icons/react/dist/ssr/LockSimple"
import { useState } from "react"

import type { SlackChannelOption } from "@/lib/api"
import {
  normalizeSlackChannelId,
  rankSlackChannels,
  rememberSlackChannels,
  slackChannelMatches,
  useRecentSlackChannels,
  useSlackChannelDirectory,
} from "@/lib/slack-channels"

export function SlackChannelIcon({
  channel,
}: {
  channel: SlackChannelOption | undefined
}) {
  const Icon = channel?.is_private
    ? LockSimpleIcon
    : channel?.is_ext_shared
      ? GlobeRegularIcon
      : HashIcon
  return (
    <Icon className="shrink-0 text-icon-secondary" size={14} weight="regular" />
  )
}

export function SlackChannelRow({
  channel,
  id,
  recent = false,
}: {
  channel: SlackChannelOption | undefined
  id: string
  /** Chosen recently in this browser. */
  recent?: boolean
}) {
  return (
    <>
      <SlackChannelIcon channel={channel} />
      <span className="min-w-0 flex-1 truncate">{channel?.name ?? id}</span>
      {recent && (
        <ClockCounterClockwiseIcon
          aria-label="Recently used"
          className="shrink-0 text-icon-secondary"
          size={12}
        />
      )}
      {channel && !channel.is_member ? (
        <span className="shrink-0 text-secondary">bot not in channel</span>
      ) : channel?.num_members != null ? (
        <span className="shrink-0 text-secondary">{channel.num_members}</span>
      ) : null}
    </>
  )
}

interface ChannelItems {
  items: Array<string>
  byId: Map<string, SlackChannelOption>
  recent: Set<string>
  label: (id: string) => string
  filter: (ids: Array<string>, query: string) => Array<string>
  setQuery: (query: string) => void
  emptyMessage: string
  refresh: () => void
  isRefreshing: boolean
}

export function RefreshSlackChannels({
  refresh,
  isRefreshing,
}: Pick<ChannelItems, "refresh" | "isRefreshing">) {
  return (
    <Button
      color="secondary"
      variant="plain"
      size="xs"
      disabled={isRefreshing}
      leftDecorator={isRefreshing ? SpinnerIcon : ArrowClockwiseIcon}
      onClick={() => refresh()}
    >
      {isRefreshing ? "Refreshing channels…" : "Refresh channels"}
    </Button>
  )
}

function useChannelItems(
  selected: Array<string>,
  eligible: ((channel: SlackChannelOption) => boolean) | undefined
): ChannelItems {
  const directory = useSlackChannelDirectory(true)
  const recentIds = useRecentSlackChannels()
  const recent = new Set(recentIds)
  const [query, setQuery] = useState("")
  const channels = directory.data?.channels ?? []
  const byId = new Map(channels.map((channel) => [channel.id, channel]))
  const pasted = normalizeSlackChannelId(query)
  const items = rankSlackChannels(
    channels.filter((channel) => !eligible || eligible(channel)),
    "",
    recentIds
  ).map((channel) => channel.id)
  const known = new Set(items)
  for (const id of [...selected, ...(pasted ? [pasted] : [])]) {
    if (!known.has(id)) {
      known.add(id)
      items.push(id)
    }
  }
  const label = (id: string) => {
    const channel = byId.get(id)
    return channel ? `#${channel.name}` : id
  }
  const filter = (ids: Array<string>, text: string) =>
    ids.filter((id) =>
      slackChannelMatches(byId.get(id) ?? { id, name: id }, text)
    )
  return {
    items,
    byId,
    recent,
    label,
    filter,
    setQuery,
    refresh: directory.refresh,
    isRefreshing: directory.isLoading || directory.isRefreshing,
    emptyMessage:
      directory.isLoading ||
      (directory.isRefreshing && !directory.data?.channels.length)
        ? "Loading channels…"
        : directory.isError
          ? "Could not load channels from Slack. Paste a channel ID."
          : "No channels match. Paste a channel ID to add one directly.",
  }
}

interface CommonProps {
  placeholder?: string
  disabled?: boolean
  /** Which directory channels to offer; selected and pasted ids always show. */
  eligible?: (channel: SlackChannelOption) => boolean
  "aria-label"?: string
  className?: string
}

function channelTypeaheadProps(
  channels: ChannelItems,
  { placeholder, disabled, className, ...props }: CommonProps
) {
  return {
    options: channels.items,
    placeholder,
    disabled,
    className,
    "aria-label": props["aria-label"],
    size: "md" as const,
    emptyText: channels.emptyMessage,
    getOptionLabel: channels.label,
    onInputChange: channels.setQuery,
    filterOptions: (
      ids: Array<string>,
      { inputValue }: { inputValue: string }
    ) => channels.filter(ids, inputValue),
    renderOption: (id: string, { selected }: { selected: boolean }) => (
      <>
        <SlackChannelRow
          channel={channels.byId.get(id)}
          id={id}
          recent={channels.recent.has(id)}
        />
        <CheckIcon
          aria-hidden
          className={
            selected ? "shrink-0 text-icon-brand" : "invisible shrink-0"
          }
          size={14}
          weight="bold"
        />
      </>
    ),
    listFooter: (
      <div className="mt-space-1 border-t border-subtle pt-space-1">
        <RefreshSlackChannels {...channels} />
      </div>
    ),
  }
}

export function SlackChannelCombobox({
  value,
  onValueChange,
  placeholder = "Search channels",
  eligible,
  ...props
}: CommonProps & {
  value: string | null
  onValueChange: (value: string | null) => void
}) {
  const channels = useChannelItems(value ? [value] : [], eligible)
  return (
    <Typeahead<string>
      {...channelTypeaheadProps(channels, { placeholder, ...props })}
      // The input keeps showing the chosen channel; list everything until the user edits it.
      filterOptions={(ids, { inputValue }) =>
        value !== null && inputValue === channels.label(value)
          ? ids
          : channels.filter(ids, inputValue)
      }
      value={value}
      onChange={(next) => {
        if (next) rememberSlackChannels([next])
        onValueChange(next ?? null)
      }}
    />
  )
}

export function SlackChannelMultiCombobox({
  value,
  onValueChange,
  placeholder = "Search channels",
  eligible,
  ...props
}: CommonProps & {
  value: Array<string>
  onValueChange: (value: Array<string>) => void
}) {
  const channels = useChannelItems(value, eligible)
  return (
    <Typeahead<string>
      {...channelTypeaheadProps(channels, { placeholder, ...props })}
      multiple
      disableCloseOnSelect
      value={value}
      onChange={(next) => {
        rememberSlackChannels(next.filter((id) => !value.includes(id)))
        onValueChange(next)
      }}
    />
  )
}
