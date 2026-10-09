import { CheckIcon, GlobeRegularIcon } from "@langchain/macaw-components/icons"
import { Button } from "@langchain/macaw-components/Button"
import { SpinnerIcon } from "@langchain/macaw-components/Spinner"
import { Typeahead } from "@langchain/macaw-components/Typeahead"
import { ArrowClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowClockwise"
import { HashIcon } from "@phosphor-icons/react/dist/ssr/Hash"
import { LockSimpleIcon } from "@phosphor-icons/react/dist/ssr/LockSimple"
import { useMemo, useState } from "react"

import type { SlackChannelOption } from "@/lib/api"
import {
  normalizeSlackChannelId,
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

/** Name or id match, ignoring a leading `#` the way Slack's switcher does. */
export function slackChannelMatches(
  channel: Pick<SlackChannelOption, "id" | "name">,
  query: string
): boolean {
  const needle = query.trim().replace(/^#/, "").toLowerCase()
  if (!needle) return true
  return (
    channel.name.toLowerCase().includes(needle) ||
    channel.id.toLowerCase().includes(needle)
  )
}

export function SlackChannelRow({
  channel,
  id,
}: {
  channel: SlackChannelOption | undefined
  id: string
}) {
  return (
    <>
      <SlackChannelIcon channel={channel} />
      <span className="min-w-0 flex-1 truncate">{channel?.name ?? id}</span>
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
  const [query, setQuery] = useState("")
  const byId = useMemo(
    () =>
      new Map(
        (directory.data?.channels ?? []).map((channel) => [channel.id, channel])
      ),
    [directory.data]
  )
  const pasted = normalizeSlackChannelId(query)
  const items = useMemo(() => {
    const ids = (directory.data?.channels ?? [])
      .filter((channel) => !eligible || eligible(channel))
      .map((channel) => channel.id)
    const known = new Set(ids)
    for (const id of [...selected, ...(pasted ? [pasted] : [])]) {
      if (!known.has(id)) {
        known.add(id)
        ids.push(id)
      }
    }
    return ids
  }, [directory.data, eligible, selected, pasted])
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
    label,
    filter,
    setQuery,
    refresh: directory.refresh,
    isRefreshing: directory.isLoading || directory.isRefreshing,
    emptyMessage: directory.isLoading
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
        <SlackChannelRow channel={channels.byId.get(id)} id={id} />
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
      onChange={(next) => onValueChange(next ?? null)}
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
      onChange={onValueChange}
    />
  )
}
