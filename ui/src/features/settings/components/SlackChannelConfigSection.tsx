import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  HashIcon,
  LockSimpleIcon,
  MagnifyingGlassIcon,
  PlusIcon,
} from "@phosphor-icons/react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"
import type { SlackChannelConfig, SlackChannelConfigUpdate } from "@/lib/api"
import {
  slackChannelDirectoryKey,
  slackChannelLabel,
  useSlackChannelDirectory,
} from "./WorkspaceBindingPickers"

const QUERY_KEY = ["slackChannelConfigs"]
const MAX_INSTRUCTIONS_CHARS = 4000

function useSaveChannelConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      channelId,
      body,
    }: {
      channelId: string
      body: SlackChannelConfigUpdate
    }) => api.saveSlackChannelConfig(channelId, body),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: QUERY_KEY })
      await qc.invalidateQueries({ queryKey: slackChannelDirectoryKey })
    },
  })
}

export function SlackChannelConfigSection() {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const configs = useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.listSlackChannelConfigs,
  })
  const directory = useSlackChannelDirectory(true)
  const add = useSaveChannelConfig()

  const configured = new Set(configs.data?.map((row) => row.channel_id))
  const matches = directory.data?.channels.filter(
    (channel) =>
      !configured.has(channel.id) &&
      `${channel.name} ${channel.id}`
        .toLowerCase()
        .includes(search.trim().toLowerCase())
  )

  return (
    <div
      aria-labelledby="slack-channel-configs-heading"
      className="border-t p-4"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <h3
            id="slack-channel-configs-heading"
            className="text-sm font-medium"
          >
            Channel settings
          </h3>
          <p className="text-xs/relaxed text-muted-foreground">
            Give channels standing instructions, and react with :merged: or :x:
            when a pull request posted there closes.
          </p>
        </div>
        <Popover
          open={open}
          onOpenChange={(value) => {
            if (add.isPending) return
            setOpen(value)
            setSearch("")
            add.reset()
          }}
        >
          <PopoverTrigger
            render={
              <Button
                size="sm"
                variant="outline"
                disabled={configs.isPending || configs.isError}
              />
            }
          >
            <PlusIcon size={14} /> Add channel
          </PopoverTrigger>
          <PopoverPopup align="end" className="w-80 p-0">
            <PopoverTitle className="px-3 pt-3 pb-2">
              Configure a channel
            </PopoverTitle>
            <div className="relative mx-3 mb-2">
              <MagnifyingGlassIcon
                className="pointer-events-none absolute top-2.5 left-2.5 text-muted-foreground"
                size={14}
              />
              <Input
                aria-label="Search Slack channels"
                placeholder="Search channels…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="pl-8"
              />
            </div>
            <div
              className="max-h-64 overflow-y-auto px-1 pb-1"
              aria-label="Slack channels"
            >
              {directory.isPending && (
                <p className="p-3 text-xs text-muted-foreground">
                  Loading channels…
                </p>
              )}
              {directory.error && (
                <p role="alert" className="p-3 text-xs text-destructive">
                  {directory.error.message}
                </p>
              )}
              {matches?.length === 0 && (
                <p className="p-3 text-xs text-muted-foreground">
                  No matching channels found.
                </p>
              )}
              {matches?.map((channel) => (
                <button
                  key={channel.id}
                  type="button"
                  disabled={add.isPending}
                  onClick={() =>
                    add.mutate(
                      {
                        channelId: channel.id,
                        body: { watch_pull_requests: true, instructions: "" },
                      },
                      { onSuccess: () => setOpen(false) }
                    )
                  }
                  className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-xs transition-colors focus-visible:outline-2 focus-visible:outline-ring enabled:hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {channel.is_private ? (
                    <LockSimpleIcon size={14} />
                  ) : (
                    <HashIcon size={14} />
                  )}
                  <span className="min-w-0 flex-1 truncate">
                    {channel.name}
                  </span>
                </button>
              ))}
            </div>
            {add.error && (
              <p role="alert" className="px-3 pb-3 text-xs text-destructive">
                {add.error.message}
              </p>
            )}
          </PopoverPopup>
        </Popover>
      </div>
      {configs.error && (
        <p role="alert" className="mt-3 text-xs text-destructive">
          {configs.error.message}
        </p>
      )}
      {configs.isPending && (
        <p className="py-6 text-xs text-muted-foreground">
          Loading channel settings…
        </p>
      )}
      {configs.data?.length === 0 && (
        <p className="mt-4 rounded-lg border border-dashed px-4 py-5 text-xs text-muted-foreground">
          No channels are configured.
        </p>
      )}
      {!!configs.data?.length && (
        <ul className="mt-4 divide-y rounded-lg border">
          {configs.data.map((config) => (
            <ChannelConfigRow
              key={`${config.channel_id}:${config.updated_at ?? ""}`}
              config={config}
              label={slackChannelLabel(directory.data, config.channel_id)}
              isMember={
                directory.data?.channels.find(
                  (channel) => channel.id === config.channel_id
                )?.is_member
              }
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function ChannelConfigRow({
  config,
  label,
  isMember,
}: {
  config: SlackChannelConfig
  label: string
  isMember: boolean | undefined
}) {
  const qc = useQueryClient()
  const [instructions, setInstructions] = useState(config.instructions)
  const save = useSaveChannelConfig()
  const remove = useMutation({
    mutationFn: () => api.removeSlackChannelConfig(config.channel_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })
  const pending = save.isPending || remove.isPending
  const dirty = instructions !== config.instructions
  const error = save.error || remove.error

  return (
    <li className="space-y-3 px-3 py-3">
      <div className="flex items-center gap-3">
        <p
          className="min-w-0 flex-1 truncate text-sm font-medium"
          title={label}
        >
          {label}
        </p>
        <Button
          size="sm"
          variant="ghost"
          aria-label={`Remove ${label}`}
          disabled={pending}
          onClick={() => remove.mutate()}
        >
          {remove.isPending ? "Removing…" : "Remove"}
        </Button>
      </div>
      <label className="flex items-center justify-between gap-4 text-xs">
        <span>
          <span className="block font-medium">Watch pull requests</span>
          <span className="block text-muted-foreground">
            React :merged: when a linked pull request merges, :x: when it closes
            unmerged.
          </span>
        </span>
        <Switch
          aria-label={`Watch pull requests in ${label}`}
          checked={config.watch_pull_requests}
          disabled={pending}
          onCheckedChange={(next) =>
            save.mutate({
              channelId: config.channel_id,
              body: {
                watch_pull_requests: next,
                instructions: config.instructions,
              },
            })
          }
        />
      </label>
      {config.watch_pull_requests && isMember === false && (
        <p className="text-xs text-destructive">
          Open SWE is not in this channel. Invite it so it can see new messages.
        </p>
      )}
      <form
        className="space-y-2"
        onSubmit={(event) => {
          event.preventDefault()
          save.mutate({
            channelId: config.channel_id,
            body: {
              watch_pull_requests: config.watch_pull_requests,
              instructions,
            },
          })
        }}
      >
        <label
          htmlFor={`channel-instructions-${config.channel_id}`}
          className="block text-xs font-medium"
        >
          Instructions
        </label>
        <Textarea
          id={`channel-instructions-${config.channel_id}`}
          placeholder="Added to every Open SWE thread that works in or for this channel."
          value={instructions}
          maxLength={MAX_INSTRUCTIONS_CHARS}
          disabled={pending}
          onChange={(event) => setInstructions(event.target.value)}
        />
        {dirty && (
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={pending}>
              {save.isPending ? "Saving…" : "Save"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => setInstructions(config.instructions)}
            >
              Cancel
            </Button>
          </div>
        )}
      </form>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error.message}
        </p>
      )}
    </li>
  )
}
