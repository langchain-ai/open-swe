import { Fragment, useMemo, useState } from "react"
import { GlobeIcon, HashIcon, LockSimpleIcon } from "@phosphor-icons/react"

import {
  Combobox,
  ComboboxChip,
  ComboboxChips,
  ComboboxChipsInput,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
  ComboboxValue,
  useComboboxAnchor,
} from "@/components/ui/combobox"
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
  if (channel?.is_private) return <LockSimpleIcon />
  if (channel?.is_ext_shared) return <GlobeIcon />
  return <HashIcon />
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
        <span className="shrink-0 pr-5 text-muted-foreground">
          bot not in channel
        </span>
      ) : channel?.num_members != null ? (
        <span className="shrink-0 pr-5 text-muted-foreground">
          {channel.num_members}
        </span>
      ) : null}
    </>
  )
}

interface ChannelItems {
  items: Array<string>
  byId: Map<string, SlackChannelOption>
  label: (id: string) => string
  filter: (id: string, query: string) => boolean
  setQuery: (query: string) => void
  emptyMessage: string
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
  const filter = (id: string, text: string) =>
    slackChannelMatches(byId.get(id) ?? { id, name: id }, text)
  return {
    items,
    byId,
    label,
    filter,
    setQuery,
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

export function SlackChannelCombobox({
  value,
  onValueChange,
  placeholder = "Search channels",
  disabled,
  eligible,
  className,
  ...props
}: CommonProps & {
  value: string | null
  onValueChange: (value: string | null) => void
}) {
  const channels = useChannelItems(value ? [value] : [], eligible)
  return (
    <Combobox<string>
      items={channels.items}
      value={value}
      onValueChange={onValueChange}
      onInputValueChange={channels.setQuery}
      itemToStringLabel={channels.label}
      filter={channels.filter}
      autoHighlight
      disabled={disabled}
    >
      <ComboboxInput
        placeholder={placeholder}
        aria-label={props["aria-label"]}
        disabled={disabled}
        showClear={value !== null}
        className={className}
      />
      <ComboboxContent>
        <ComboboxEmpty>{channels.emptyMessage}</ComboboxEmpty>
        <ComboboxList>
          {(id: string) => (
            <ComboboxItem key={id} value={id}>
              <SlackChannelRow channel={channels.byId.get(id)} id={id} />
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  )
}

export function SlackChannelMultiCombobox({
  value,
  onValueChange,
  placeholder = "Search channels",
  disabled,
  eligible,
  className,
  ...props
}: CommonProps & {
  value: Array<string>
  onValueChange: (value: Array<string>) => void
}) {
  const channels = useChannelItems(value, eligible)
  const anchor = useComboboxAnchor()
  return (
    <Combobox<string, true>
      multiple
      items={channels.items}
      value={value}
      onValueChange={onValueChange}
      onInputValueChange={channels.setQuery}
      itemToStringLabel={channels.label}
      filter={channels.filter}
      autoHighlight
      disabled={disabled}
    >
      <ComboboxChips ref={anchor} className={className}>
        <ComboboxValue>
          {(ids: Array<string>) => (
            <Fragment>
              {ids.map((id) => (
                <ComboboxChip key={id}>{channels.label(id)}</ComboboxChip>
              ))}
              <ComboboxChipsInput
                placeholder={ids.length === 0 ? placeholder : undefined}
                aria-label={props["aria-label"]}
                disabled={disabled}
              />
            </Fragment>
          )}
        </ComboboxValue>
      </ComboboxChips>
      <ComboboxContent anchor={anchor}>
        <ComboboxEmpty>{channels.emptyMessage}</ComboboxEmpty>
        <ComboboxList>
          {(id: string) => (
            <ComboboxItem key={id} value={id}>
              <SlackChannelRow channel={channels.byId.get(id)} id={id} />
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  )
}
