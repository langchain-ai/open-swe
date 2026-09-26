import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"

const QUERY_KEY = ["untaggedSlackChannels"]

export function UntaggedSlackChannelsSection() {
  const qc = useQueryClient()
  const [selected, setSelected] = useState("")
  const channels = useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.listUntaggedChannels,
  })
  const directory = useQuery({
    queryKey: ["slackChannelDirectory"],
    queryFn: api.listSlackChannels,
  })
  const enable = useMutation({
    mutationFn: api.enableUntaggedChannel,
    onSuccess: () => {
      setSelected("")
      void qc.invalidateQueries({ queryKey: QUERY_KEY })
    },
  })
  const disable = useMutation({
    mutationFn: api.disableUntaggedChannel,
    onSuccess: () => void qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })
  const names = new Map(
    directory.data?.channels.map((channel) => [channel.id, channel.name])
  )
  const enabled = new Set(channels.data?.map((channel) => channel.channel_id))

  return (
    <div className="space-y-3 border-t border-border p-4">
      <div>
        <h3 className="text-sm font-medium">Untagged channel messages</h3>
        <p className="text-xs/relaxed text-muted-foreground">
          Opt in individual Slack channels: a top-level message starts a thread,
          and replies continue it without mentioning Open SWE. Other channels
          still require a mention.
        </p>
      </div>
      <div className="flex gap-2">
        <select
          aria-label="Channel to enable untagged messages"
          className="min-w-0 flex-1 rounded-md border border-border bg-background px-2 py-1 text-sm"
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
        >
          <option value="">Choose a channel</option>
          {directory.data?.channels
            .filter(
              (channel) =>
                channel.is_member &&
                !channel.is_ext_shared &&
                !channel.is_pending_ext_shared &&
                !enabled.has(channel.id)
            )
            .map((channel) => (
              <option key={channel.id} value={channel.id}>
                #{channel.name}
              </option>
            ))}
        </select>
        <Button
          size="sm"
          variant="outline"
          disabled={!selected || enable.isPending}
          onClick={() => enable.mutate(selected)}
        >
          Enable
        </Button>
      </div>
      {directory.isError && (
        <p role="alert" className="text-xs text-destructive">
          Could not load Slack channels.
        </p>
      )}
      {channels.isError && (
        <p role="alert" className="text-xs text-destructive">
          Could not load enabled channels.
        </p>
      )}
      {enable.isError && (
        <p role="alert" className="text-xs text-destructive">
          Could not enable channel.
        </p>
      )}
      {channels.data?.map((channel) => (
        <div
          key={channel.channel_id}
          className="flex items-center justify-between gap-2 text-sm"
        >
          <span>#{names.get(channel.channel_id) ?? channel.channel_id}</span>
          <Button
            size="sm"
            variant="ghost"
            disabled={disable.isPending}
            onClick={() => disable.mutate(channel.channel_id)}
          >
            Disable
          </Button>
        </div>
      ))}
    </div>
  )
}
