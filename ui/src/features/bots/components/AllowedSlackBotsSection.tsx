import {
  CaretRightIcon,
  MagnifyingGlassRegularIcon,
  PlusIcon,
} from "@langchain/macaw-components/icons"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Avatar } from "@langchain/macaw-components/Avatar"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { Input } from "@langchain/macaw-components/Input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { Text } from "@langchain/macaw-components/Text"
import { RobotIcon } from "@phosphor-icons/react/dist/ssr/Robot"
import { useState } from "react"

import { api } from "@/lib/api"
import type { AllowedSlackBot } from "@/lib/api"

const QUERY_KEY = ["allowedSlackBots"]
/** What every user's Bots page lists; it follows the allowlist. */
const DIRECTORY_QUERY_KEY = ["allowedSlackBotDirectory"]

const sameBot = (a: AllowedSlackBot, b: AllowedSlackBot) =>
  a.team_id === b.team_id && a.bot_id === b.bot_id

export function AllowedSlackBotsSection({
  onBotChange,
}: {
  onBotChange?: (bot: string) => void
}) {
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
    meta: { silent: true },
    mutationFn: api.allowSlackBot,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: QUERY_KEY })
      void qc.invalidateQueries({ queryKey: DIRECTORY_QUERY_KEY })
      setOpen(false)
      setBotId("")
      setSearch("")
    },
  })
  const remove = useMutation({
    meta: { errorTitle: "Couldn't remove Slack bot" },
    mutationFn: (bot: AllowedSlackBot) =>
      api.removeAllowedSlackBot(bot.team_id, bot.bot_id),
    onMutate: async (bot) => {
      await qc.cancelQueries({ queryKey: QUERY_KEY })
      qc.setQueryData<Array<AllowedSlackBot>>(QUERY_KEY, (current) =>
        current?.filter((b) => !sameBot(b, bot))
      )
    },
    onError: (_error, bot) =>
      qc.setQueryData<Array<AllowedSlackBot>>(QUERY_KEY, (current) =>
        current && !current.some((b) => sameBot(b, bot))
          ? [...current, bot]
          : current
      ),
    onSettled: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: QUERY_KEY }),
        qc.invalidateQueries({ queryKey: DIRECTORY_QUERY_KEY }),
      ]),
  })

  const pending = add.isPending
  const unavailable = pending || bots.isPending || bots.isError
  const matches = directory.data?.filter((bot) =>
    `${bot.name} ${bot.bot_id} ${bot.user_id}`
      .toLowerCase()
      .includes(search.trim().toLowerCase())
  )
  const allow = (id: string) => {
    if (!id || unavailable) return
    add.mutate({ bot_id: id })
  }

  return (
    <div aria-labelledby="allowed-slack-bots-heading" className="p-space-4">
      <div className="flex items-start justify-between gap-space-4">
        <div className="space-y-space-1">
          <h3
            id="allowed-slack-bots-heading"
            className="text-sm font-medium text-primary"
          >
            Enabled bots
          </h3>
          <p className="text-xs/relaxed text-secondary">
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
          <PopoverTrigger asChild>
            <Button
              color="secondary"
              variant="outlined"
              leftDecorator={PlusIcon}
              disabled={unavailable}
            >
              Add bot
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-80 p-0">
            <Text
              as="h4"
              variant="sm"
              weight="medium"
              className="px-space-3 pt-space-3 pb-space-2"
            >
              Add a Slack bot
            </Text>
            {manual ? (
              <form
                className="space-y-space-3 px-space-3 pb-space-3"
                onSubmit={(event) => {
                  event.preventDefault()
                  allow(botId.trim())
                }}
              >
                <Input
                  size="md"
                  id="allowed-slack-bot-id"
                  label="Slack bot ID"
                  placeholder="B0123456789 or U0123456789"
                  value={botId}
                  onChange={setBotId}
                  disabled={pending}
                />
                <Button type="submit" disabled={!botId.trim() || unavailable}>
                  {add.isPending ? "Verifying…" : "Allow bot"}
                </Button>
              </form>
            ) : (
              <>
                <Input
                  size="md"
                  aria-label="Search Slack bots"
                  leftIcon={MagnifyingGlassRegularIcon}
                  placeholder="Search Slack bots…"
                  value={search}
                  onChange={setSearch}
                  className="px-space-3 pb-space-2"
                />
                <div
                  className="max-h-64 overflow-y-auto px-space-1 pb-space-1"
                  aria-label="Slack bots"
                >
                  {directory.isPending && (
                    <p className="p-space-3 text-xs text-secondary">
                      Loading Slack bots…
                    </p>
                  )}
                  {directory.error && (
                    <div className="space-y-space-2 p-space-3">
                      <p role="alert" className="text-xs text-error-secondary">
                        {directory.error.message}
                      </p>
                      <Button
                        color="secondary"
                        variant="outlined"
                        disabled={directory.isFetching}
                        onClick={() => void directory.refetch()}
                      >
                        Retry
                      </Button>
                    </div>
                  )}
                  {matches?.length === 0 && (
                    <p className="p-space-3 text-xs text-secondary">
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
                        className="flex w-full items-center gap-space-3 rounded-md px-space-2 py-space-2 text-left transition-colors focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none enabled:hover:bg-elevated-hover disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <span aria-hidden="true">
                          <Avatar
                            size="sm"
                            label={bot.name}
                            imageUrl={bot.image_url || undefined}
                          />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span
                            className="block truncate text-xs font-medium text-primary"
                            title={bot.name}
                          >
                            {bot.name}
                          </span>
                          <span className="block text-xxs text-secondary">
                            {bot.bot_id}
                          </span>
                        </span>
                        <span className="px-space-2 text-xs" aria-hidden="true">
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
              <p
                role="alert"
                className="px-space-3 pb-space-3 text-xs text-error-secondary"
              >
                {add.error.message}
              </p>
            )}
            <div className="border-t border-default px-space-3 py-space-2">
              <Button
                color="secondary"
                variant="underlined"
                size="xs"
                disabled={pending}
                onClick={() => {
                  setManual(!manual)
                  add.reset()
                }}
              >
                {manual ? "Browse Slack bots" : "Enter bot ID manually"}
              </Button>
            </div>
          </PopoverContent>
        </Popover>
      </div>
      {bots.error && (
        <div role="alert" className="mt-space-3">
          <Banner intent="error">{bots.error.message}</Banner>
        </div>
      )}
      {bots.isPending && (
        <p className="py-space-5 text-xs text-secondary">
          Loading allowed bots…
        </p>
      )}
      {bots.data?.length === 0 && (
        <EmptyState
          size="sm"
          icon={RobotIcon}
          title="No Slack bots are allowed."
          description="Add a bot from your Slack workspace to get started."
          className="mt-space-4 rounded-lg border border-dashed border-default"
        />
      )}
      {!!bots.data?.length && (
        <ul className="mt-space-4 divide-y divide-default rounded-lg border border-default">
          {bots.data.map((bot) => (
            <li
              key={`${bot.team_id}:${bot.bot_id}`}
              className="flex items-center gap-space-3 px-space-3 py-space-3"
            >
              <button
                type="button"
                onClick={() => onBotChange?.(`${bot.team_id}:${bot.bot_id}`)}
                className="flex min-w-0 flex-1 items-center gap-space-3 rounded-md py-space-1 text-left hover:bg-surface-level-1-hover focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none"
                disabled={!onBotChange}
              >
                <span aria-hidden="true">
                  <Avatar
                    size="md"
                    label={bot.name}
                    imageUrl={bot.image_url || undefined}
                  />
                </span>
                <div className="min-w-0 flex-1">
                  <p
                    className="truncate text-sm font-medium text-primary"
                    title={bot.name}
                  >
                    {bot.name}
                  </p>
                  <p className="text-xs text-secondary">{bot.bot_id}</p>
                </div>
                <span className="text-xs text-success-secondary">Enabled</span>
                {onBotChange && (
                  <CaretRightIcon
                    aria-hidden="true"
                    size={14}
                    weight="regular"
                    className="text-icon-secondary"
                  />
                )}
              </button>
              <Button
                color="secondary"
                variant="plain"
                aria-label={`Remove ${bot.name}`}
                disabled={pending}
                onClick={() => {
                  add.reset()
                  remove.mutate(bot)
                }}
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-space-3 flex items-center justify-between gap-space-3 text-xs text-secondary">
        <span>
          {bots.data
            ? `${bots.data.length} ${bots.data.length === 1 ? "bot" : "bots"} allowed`
            : ""}
        </span>
        <span className="flex items-center gap-space-2">
          <RobotIcon size={13} weight="regular" /> Runs as Open SWE
        </span>
      </div>
      <p className="mt-space-2 text-xs/relaxed text-secondary">
        Uses the Open SWE GitHub App’s repository access. Bots can only continue
        tasks they started.
      </p>
    </div>
  )
}
