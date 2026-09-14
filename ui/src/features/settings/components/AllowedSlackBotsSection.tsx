import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus, Robot, MagnifyingGlass } from "@phosphor-icons/react"
import { useState } from "react"

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover"
import { api } from "@/lib/api"
import type { AllowedSlackBot } from "@/lib/api"

const QUERY_KEY = ["allowedSlackBots"]

export function AllowedSlackBotsSection() {
  const [open, setOpen] = useState(false)
  const [manual, setManual] = useState(false)
  const [botId, setBotId] = useState("")
  const [search, setSearch] = useState("")
  const qc = useQueryClient()
  const bots = useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.listAllowedSlackBots,
  })
  const directory = useQuery({
    queryKey: ["slackBotDirectory"],
    queryFn: api.listSlackBots,
    enabled: open && !manual,
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  const add = useMutation({
    mutationFn: api.allowSlackBot,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: QUERY_KEY })
      setOpen(false)
      setBotId("")
      setSearch("")
    },
  })
  const remove = useMutation({
    mutationFn: (bot: AllowedSlackBot) =>
      api.removeAllowedSlackBot(bot.team_id, bot.bot_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })

  const pending = add.isPending || remove.isPending
  const unavailable = pending || bots.isPending || bots.isError
  const error = remove.error || bots.error
  const matches = directory.data?.filter((bot) =>
    `${bot.name} ${bot.bot_id} ${bot.user_id}`
      .toLowerCase()
      .includes(search.trim().toLowerCase())
  )
  const allow = (id: string) => {
    if (!id || unavailable) return
    remove.reset()
    add.mutate({ bot_id: id })
  }

  return (
    <div aria-labelledby="allowed-slack-bots-heading" className="p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <h3 id="allowed-slack-bots-heading" className="text-sm font-medium">
            Allowed bots
          </h3>
          <p className="text-xs/relaxed text-muted-foreground">
            Let trusted Slack bots start a task by mentioning Open SWE.
          </p>
        </div>
        <Popover
          open={open}
          onOpenChange={(value) => {
            if (pending) return
            setOpen(value)
            setSearch("")
            setBotId("")
            setManual(false)
            add.reset()
          }}
        >
          <PopoverTrigger
            render={
              <Button size="sm" variant="outline" disabled={unavailable} />
            }
          >
            <Plus size={14} /> Add bot
          </PopoverTrigger>
          <PopoverPopup align="end" className="w-80 p-0">
            <PopoverTitle className="px-3 pt-3 pb-2">
              Add a Slack bot
            </PopoverTitle>
            {manual ? (
              <form
                className="space-y-3 px-3 pb-3"
                onSubmit={(event) => {
                  event.preventDefault()
                  allow(botId.trim())
                }}
              >
                <label
                  htmlFor="allowed-slack-bot-id"
                  className="text-xs text-muted-foreground"
                >
                  Slack bot ID
                </label>
                <Input
                  id="allowed-slack-bot-id"
                  placeholder="B0123456789 or U0123456789"
                  value={botId}
                  onChange={(event) => setBotId(event.target.value)}
                  disabled={pending}
                />
                <Button
                  type="submit"
                  size="sm"
                  disabled={!botId.trim() || unavailable}
                >
                  {add.isPending ? "Verifying…" : "Allow bot"}
                </Button>
              </form>
            ) : (
              <>
                <div className="relative mx-3 mb-2">
                  <MagnifyingGlass
                    className="pointer-events-none absolute top-2.5 left-2.5 text-muted-foreground"
                    size={14}
                  />
                  <Input
                    aria-label="Search Slack bots"
                    placeholder="Search Slack bots…"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    className="pl-8"
                  />
                </div>
                <div
                  className="max-h-64 overflow-y-auto px-1 pb-1"
                  aria-label="Slack bots"
                >
                  {directory.isPending && (
                    <p className="p-3 text-xs text-muted-foreground">
                      Loading Slack bots…
                    </p>
                  )}
                  {directory.error && (
                    <div className="space-y-2 p-3">
                      <p role="alert" className="text-xs text-destructive">
                        {directory.error.message}
                      </p>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={directory.isFetching}
                        onClick={() => void directory.refetch()}
                      >
                        Retry
                      </Button>
                    </div>
                  )}
                  {matches?.length === 0 && (
                    <p className="p-3 text-xs text-muted-foreground">
                      No matching bots found.
                    </p>
                  )}
                  {matches?.map((bot) => {
                    const allowed = bots.data?.some(
                      (item) =>
                        item.team_id === bot.team_id &&
                        item.bot_id === bot.bot_id
                    )
                    return (
                      <button
                        key={`${bot.team_id}:${bot.bot_id}`}
                        type="button"
                        disabled={allowed || unavailable}
                        aria-label={
                          allowed
                            ? `${bot.name} is already allowed`
                            : `Allow ${bot.name}`
                        }
                        onClick={() => allow(bot.user_id)}
                        className="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left transition-colors focus-visible:outline-2 focus-visible:outline-ring enabled:hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Avatar size="sm">
                          <AvatarImage src={bot.image_url} alt="" />
                          <AvatarFallback>
                            {bot.name.slice(0, 1).toUpperCase()}
                          </AvatarFallback>
                        </Avatar>
                        <span className="min-w-0 flex-1">
                          <span
                            className="block truncate text-xs font-medium"
                            title={bot.name}
                          >
                            {bot.name}
                          </span>
                          <span className="block text-[11px] text-muted-foreground">
                            {bot.bot_id}
                          </span>
                        </span>
                        <span className="px-2 text-xs" aria-hidden="true">
                          {allowed
                            ? "Allowed"
                            : add.isPending &&
                                add.variables.bot_id === bot.user_id
                              ? "Verifying…"
                              : "Allow"}
                        </span>
                      </button>
                    )
                  })}
                </div>
              </>
            )}
            {add.error && (
              <p role="alert" className="px-3 pb-3 text-xs text-destructive">
                {add.error.message}
              </p>
            )}
            <div className="border-t px-3 py-2">
              <Button
                type="button"
                variant="link"
                size="sm"
                className="h-auto p-0 text-xs text-muted-foreground"
                disabled={pending}
                onClick={() => {
                  setManual(!manual)
                  add.reset()
                }}
              >
                {manual ? "Browse Slack bots" : "Enter bot ID manually"}
              </Button>
            </div>
          </PopoverPopup>
        </Popover>
      </div>
      {error && (
        <p role="alert" className="mt-3 text-xs text-destructive">
          {error.message}
        </p>
      )}
      {bots.isPending && (
        <p className="py-6 text-xs text-muted-foreground">
          Loading allowed bots…
        </p>
      )}
      {bots.data?.length === 0 && (
        <div className="mt-4 flex items-center gap-3 rounded-lg border border-dashed px-4 py-5">
          <Robot size={22} className="shrink-0 text-muted-foreground" />
          <div className="space-y-1">
            <p className="text-xs font-medium">No Slack bots are allowed.</p>
            <p className="text-xs text-muted-foreground">
              Add a bot from your Slack workspace to get started.
            </p>
          </div>
        </div>
      )}
      {!!bots.data?.length && (
        <ul className="mt-4 divide-y rounded-lg border">
          {bots.data.map((bot) => (
            <li
              key={`${bot.team_id}:${bot.bot_id}`}
              className="flex items-center gap-3 px-3 py-3"
            >
              <Avatar>
                <AvatarImage src={bot.image_url} alt="" />
                <AvatarFallback>
                  <Robot size={18} />
                </AvatarFallback>
              </Avatar>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium" title={bot.name}>
                  {bot.name}
                </p>
                <p className="text-xs text-muted-foreground">{bot.bot_id}</p>
              </div>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Remove ${bot.name}`}
                disabled={pending}
                onClick={() => {
                  add.reset()
                  remove.mutate(bot)
                }}
              >
                {remove.isPending && remove.variables.bot_id === bot.bot_id
                  ? "Removing…"
                  : "Remove"}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex items-center justify-between gap-3 text-xs text-muted-foreground">
        <span>
          {bots.data
            ? `${bots.data.length} ${bots.data.length === 1 ? "bot" : "bots"} allowed`
            : ""}
        </span>
        <span className="flex items-center gap-1.5">
          <Robot size={13} /> Runs as Open SWE
        </span>
      </div>
      <p className="mt-2 text-xs/relaxed text-muted-foreground">
        Uses the Open SWE GitHub App’s repository access. Bots can only continue
        tasks they started.
      </p>
    </div>
  )
}
