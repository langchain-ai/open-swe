import { useMemo, useRef, useState } from "react"
import type { KeyboardEvent } from "react"

import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { cn } from "@langchain/gtm-platform-design-system/ui/cn"
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
  ComboboxTrigger,
} from "@langchain/gtm-platform-design-system/ui/combobox"
import {
  Command,
  CommandEmpty,
  CommandFooter,
  CommandInput,
  CommandItem,
  CommandList,
} from "@langchain/gtm-platform-design-system/ui/command"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import {
  Popover,
  PopoverContent,
} from "@langchain/gtm-platform-design-system/ui/popover"

import { Check, Globe, Hash, Lock, RefreshCw, X } from "@/components/glyphs"
import type { Glyph } from "@/components/glyphs"
import type { SlackChannelOption } from "@/lib/api"
import {
  normalizeSlackChannelId,
  useSlackChannelDirectory,
} from "@/lib/slack-channels"

function slackChannelGlyph(channel: SlackChannelOption | undefined): Glyph {
  if (channel?.is_private) return Lock
  if (channel?.is_ext_shared) return Globe
  return Hash
}

export function SlackChannelIcon({
  channel,
}: {
  channel: SlackChannelOption | undefined
}) {
  return (
    <Icon
      icon={slackChannelGlyph(channel)}
      size="sm"
      className="text-ink-subtle"
    />
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
      <Box render={<span />} className="min-w-0 flex-1 truncate">
        {channel?.name ?? id}
      </Box>
      {channel && !channel.is_member ? (
        <Box render={<span />} className="shrink-0 text-meta text-ink-subtle">
          bot not in channel
        </Box>
      ) : channel?.num_members != null ? (
        <Box
          render={<span />}
          className="shrink-0 font-mono text-meta text-ink-subtle tabular-nums"
        >
          {channel.num_members}
        </Box>
      ) : null}
    </>
  )
}

interface ChannelItems {
  items: Array<string>
  byId: Map<string, SlackChannelOption>
  label: (id: string) => string
  filter: (id: string, query: string) => boolean
  query: string
  setQuery: (query: string) => void
  /** The channel id the query names when it is a pasted id or link. */
  pasted: string | null
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
      type="button"
      variant="ghost"
      size="compact"
      disabled={isRefreshing}
      onClick={() => refresh()}
    >
      <Icon
        icon={RefreshCw}
        size="sm"
        className={
          isRefreshing ? "animate-spin motion-reduce:animate-none" : undefined
        }
      />
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
  const filter = (id: string, text: string) =>
    id === pasted || slackChannelMatches(byId.get(id) ?? { id, name: id }, text)
  return {
    items,
    byId,
    label,
    filter,
    query,
    setQuery,
    pasted,
    refresh: directory.refresh,
    isRefreshing: directory.isLoading || directory.isRefreshing,
    emptyMessage: directory.isLoading
      ? "Loading channels…"
      : directory.isError
        ? "Could not load channels from Slack. Paste a channel ID."
        : "No channels match. Paste a channel ID to add one directly.",
  }
}

/*
 * cmdk scores an item on its value plus keywords. The channel name (with and
 * without `#`) is what people type; a pasted link only resolves to an id, so
 * that one item also carries the raw query to survive cmdk's own filter.
 */
function channelKeywords(channels: ChannelItems, id: string): string[] {
  const name = channels.byId.get(id)?.name
  const keywords = name ? [name, `#${name}`] : []
  return id === channels.pasted ? [...keywords, channels.query] : keywords
}

interface CommonProps {
  placeholder?: string
  disabled?: boolean
  /** Which directory channels to offer; selected and pasted ids always show. */
  eligible?: (channel: SlackChannelOption) => boolean
  "aria-label"?: string
  className?: string
}

/** One Slack channel, picked from the bot's directory or pasted as an id or link. */
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
  const [open, setOpen] = useState(false)
  const setOpenAndReset = (next: boolean) => {
    setOpen(next)
    if (!next) channels.setQuery("")
  }
  return (
    <Combobox
      value={value ?? undefined}
      onValueChange={onValueChange}
      open={open}
      onOpenChange={setOpenAndReset}
    >
      <ComboboxTrigger
        aria-label={props["aria-label"]}
        disabled={disabled}
        placeholder={placeholder}
        className={className}
      >
        {value ? (
          <Inline gap="sm" align="center" className="min-w-0">
            <SlackChannelIcon channel={channels.byId.get(value)} />
            <Box render={<span />} className="truncate">
              {channels.label(value)}
            </Box>
          </Inline>
        ) : null}
      </ComboboxTrigger>
      <ComboboxContent>
        <ComboboxInput
          placeholder="Search or paste a channel ID"
          value={channels.query}
          onValueChange={channels.setQuery}
        />
        <ComboboxList>
          <ComboboxEmpty>{channels.emptyMessage}</ComboboxEmpty>
          {channels.items.map((id) => (
            <ComboboxItem
              key={id}
              value={id}
              keywords={channelKeywords(channels, id)}
            >
              <SlackChannelRow channel={channels.byId.get(id)} id={id} />
            </ComboboxItem>
          ))}
        </ComboboxList>
        <CommandFooter>
          <RefreshSlackChannels {...channels} />
          {value === null ? null : (
            <Button
              type="button"
              variant="ghost"
              size="compact"
              onClick={() => {
                onValueChange(null)
                setOpenAndReset(false)
              }}
            >
              Clear channel
            </Button>
          )}
        </CommandFooter>
      </ComboboxContent>
    </Combobox>
  )
}

/** Close reasons that come from the field itself rather than from leaving it. */
const FIELD_CLOSE_REASONS = new Set(["outside-press", "focus-out"])

/**
 * Several Slack channels: chips for the picks, a search field that is also the
 * combobox, and the directory list anchored under it. The system Combobox is
 * single-select by design, so this composes its parts (Command, Popover, Badge)
 * rather than growing a second picker.
 */
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
  const [open, setOpen] = useState(false)
  const fieldRef = useRef<HTMLDivElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const selected = new Set(value)
  const visible = channels.items.filter((id) =>
    channels.filter(id, channels.query)
  )

  const toggle = (id: string) => {
    onValueChange(
      selected.has(id) ? value.filter((item) => item !== id) : [...value, id]
    )
    channels.setQuery("")
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Backspace" && !channels.query && value.length > 0) {
      onValueChange(value.slice(0, -1))
      return
    }
    if (event.key !== "Escape" && event.key !== "Tab") setOpen(true)
  }

  return (
    <Command
      shouldFilter={false}
      label={props["aria-label"]}
      className="h-auto overflow-visible rounded-none"
    >
      <Box
        ref={fieldRef}
        border="line-strong"
        radius="control"
        bg="muted"
        className={cn(
          // The field draws the edge, so the search row's own underline goes.
          "min-w-0 overflow-hidden focus-within:border-primary focus-within:ring-1 focus-within:ring-primary/40 [&_[data-slot=command-input-wrapper]]:border-b-0",
          disabled && "opacity-50",
          className
        )}
      >
        {value.length > 0 && (
          <Inline gap="xs" wrap padding="sm" className="border-b border-line">
            {value.map((id) => (
              <Badge key={id} tier="quiet" tone="neutral">
                {channels.label(id)}
                <button
                  type="button"
                  aria-label={`Remove ${channels.label(id)}`}
                  disabled={disabled}
                  className="inline-flex rounded-tick text-ink-subtle hover:text-ink"
                  onClick={() => toggle(id)}
                >
                  <Icon icon={X} size="sm" />
                </button>
              </Badge>
            ))}
          </Inline>
        )}
        <CommandInput
          placeholder={value.length === 0 ? placeholder : undefined}
          value={channels.query}
          onValueChange={(next) => {
            channels.setQuery(next)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          onBlur={(event) => {
            const next = event.relatedTarget
            if (
              next instanceof Node &&
              (fieldRef.current?.contains(next) ||
                popupRef.current?.contains(next))
            )
              return
            setOpen(false)
          }}
          disabled={disabled}
        />
      </Box>
      <Popover
        open={open && !disabled}
        onOpenChange={(next, details) => {
          const target = details.event?.target
          if (
            !next &&
            FIELD_CLOSE_REASONS.has(details.reason) &&
            target instanceof Node &&
            fieldRef.current?.contains(target)
          )
            return
          setOpen(next)
        }}
      >
        <PopoverContent
          ref={popupRef}
          anchor={fieldRef}
          align="start"
          inset="flush"
          initialFocus={false}
          finalFocus={false}
          className="w-(--anchor-width) min-w-48"
          // Picking from the list must not blur the field it types into.
          onMouseDown={(event) => event.preventDefault()}
        >
          <CommandList>
            <CommandEmpty>{channels.emptyMessage}</CommandEmpty>
            {visible.map((id) => (
              <CommandItem
                key={id}
                value={id}
                onSelect={() => toggle(id)}
                className="pr-8"
              >
                <SlackChannelRow channel={channels.byId.get(id)} id={id} />
                {selected.has(id) && (
                  <Box
                    render={<span />}
                    className="pointer-events-none absolute right-2 flex items-center"
                  >
                    <Icon icon={Check} size="sm" />
                  </Box>
                )}
              </CommandItem>
            ))}
          </CommandList>
          <CommandFooter>
            <RefreshSlackChannels {...channels} />
          </CommandFooter>
        </PopoverContent>
      </Popover>
    </Command>
  )
}
