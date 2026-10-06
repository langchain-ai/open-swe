import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { FormField } from "@langchain/gtm-platform-design-system/patterns/form-field"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Avatar } from "@langchain/gtm-platform-design-system/ui/avatar"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { HELP_CLASS, LABEL_CLASS } from "@langchain/gtm-platform-design-system/ui/label"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/gtm-platform-design-system/ui/popover"
import { SearchInput } from "@langchain/gtm-platform-design-system/ui/search-input"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"

import { AlertTriangle, Bot, ChevronRight, Plus } from "@/components/glyphs"
import { api } from "@/lib/api"
import type { AllowedSlackBot } from "@/lib/api"

const QUERY_KEY = ["allowedSlackBots"]
/** What every user's Bots page lists; it follows the allowlist. */
const DIRECTORY_QUERY_KEY = ["allowedSlackBotDirectory"]

const sameBot = (a: AllowedSlackBot, b: AllowedSlackBot) =>
  a.team_id === b.team_id && a.bot_id === b.bot_id

const PICKER_ROW_CLASS =
  "flex w-full items-center gap-2 rounded-badge px-2 py-1.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-primary enabled:hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50"

const BOT_ROW_CLASS =
  "flex min-h-row-record min-w-0 flex-1 items-center gap-3 py-2 pl-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset enabled:hover:bg-hover"

function ErrorLine({ children }: { children: string }) {
  return (
    <Inline role="alert" gap="sm" align="start" ink="risk" className="text-label">
      <Icon icon={AlertTriangle} size="sm" className="mt-0.5" />
      <Box render={<span />}>{children}</Box>
    </Inline>
  )
}

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
    <PageSection
      title="Enabled bots"
      description="Let trusted Slack bots start a task by mentioning Open SWE."
      actions={
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
              <Button size="compact" variant="outline" disabled={unavailable} />
            }
          >
            <Icon icon={Plus} size="sm" />
            Add bot
          </PopoverTrigger>
          <PopoverContent align="end" inset="flush" className="w-80">
            <Stack gap="sm" padding="md">
              <Box className={LABEL_CLASS}>Add a Slack bot</Box>
              {manual ? (
                <Stack
                  render={
                    <form
                      onSubmit={(event) => {
                        event.preventDefault()
                        allow(botId.trim())
                      }}
                    />
                  }
                  gap="md"
                >
                  <FormField
                    label="Slack bot ID"
                    error={add.error?.message}
                    control={
                      <Input
                        placeholder="B0123456789 or U0123456789"
                        value={botId}
                        onChange={(event) => setBotId(event.target.value)}
                        disabled={pending}
                      />
                    }
                  />
                  <Inline>
                    <Button
                      type="submit"
                      size="compact"
                      disabled={!botId.trim() || unavailable}
                    >
                      {add.isPending ? "Verifying…" : "Allow bot"}
                    </Button>
                  </Inline>
                </Stack>
              ) : (
                <>
                  <SearchInput
                    label="Search Slack bots"
                    placeholder="Search Slack bots…"
                    value={search}
                    onValueChange={setSearch}
                  />
                  <Stack
                    gap="none"
                    aria-label="Slack bots"
                    className="-mx-1 max-h-64 overflow-y-auto"
                  >
                    {directory.isPending && (
                      <Box className="p-2 text-meta text-ink-subtle">
                        Loading Slack bots…
                      </Box>
                    )}
                    {directory.error && (
                      <StateNotice
                        tone="RISK"
                        icon={AlertTriangle}
                        title="Slack bots could not be listed"
                        description={directory.error.message}
                        action={
                          <Button
                            size="compact"
                            variant="outline"
                            disabled={directory.isFetching}
                            onClick={() => void directory.refetch()}
                          >
                            Retry
                          </Button>
                        }
                      />
                    )}
                    {matches?.length === 0 && (
                      <Box className="p-2 text-meta text-ink-subtle">
                        No matching bots found.
                      </Box>
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
                          className={PICKER_ROW_CLASS}
                        >
                          <Avatar
                            name={bot.name}
                            src={bot.image_url || undefined}
                            size="chat"
                            aria-hidden
                          />
                          <Stack gap="none" className="min-w-0 flex-1">
                            <Box
                              className="truncate text-label font-medium text-ink"
                              title={bot.name}
                            >
                              {bot.name}
                            </Box>
                            <Box className="text-meta text-ink-subtle">
                              {bot.bot_id}
                            </Box>
                          </Stack>
                          <Box
                            render={<span />}
                            aria-hidden="true"
                            className="text-label text-ink-subtle"
                          >
                            {allowed
                              ? "Allowed"
                              : add.isPending &&
                                  add.variables.bot_id === bot.user_id
                                ? "Verifying…"
                                : "Allow"}
                          </Box>
                        </button>
                      )
                    })}
                  </Stack>
                  {add.error && <ErrorLine>{add.error.message}</ErrorLine>}
                </>
              )}
            </Stack>
            <Box className="border-t border-line px-2 py-1">
              <Button
                type="button"
                variant="ghost"
                size="compact"
                className="text-ink-subtle"
                disabled={pending}
                onClick={() => {
                  setManual(!manual)
                  add.reset()
                }}
              >
                {manual ? "Browse Slack bots" : "Enter bot ID manually"}
              </Button>
            </Box>
          </PopoverContent>
        </Popover>
      }
    >
      <Stack gap="md">
        {bots.error && <ErrorLine>{bots.error.message}</ErrorLine>}
        {bots.isPending && (
          <Stack
            gap="none"
            bg="panel"
            border="line"
            radius="panel"
            aria-busy
            aria-label="Loading allowed bots"
            className="overflow-hidden"
          >
            {[0, 1].map((row) => (
              <Inline
                key={row}
                gap="md"
                className="min-h-row-record border-b border-line px-3 py-2 last:border-b-0"
              >
                <Skeleton className="size-6 rounded-full" />
                <Skeleton className="h-4 w-40" />
              </Inline>
            ))}
          </Stack>
        )}
        {bots.data?.length === 0 && (
          <EmptyState
            icon={Bot}
            title="No Slack bots are allowed."
            description="Add a bot from your Slack workspace to get started."
          />
        )}
        {!!bots.data?.length && (
          <Stack
            render={<ul aria-label="Allowed bots" />}
            gap="none"
            bg="panel"
            border="line"
            radius="panel"
            className="overflow-hidden"
          >
            {bots.data.map((bot) => (
              <Inline
                key={`${bot.team_id}:${bot.bot_id}`}
                render={<li />}
                gap="sm"
                className="border-b border-line pr-3 last:border-b-0"
              >
                <button
                  type="button"
                  onClick={() => onBotChange?.(`${bot.team_id}:${bot.bot_id}`)}
                  className={BOT_ROW_CLASS}
                  disabled={!onBotChange}
                >
                  <Avatar
                    name={bot.name}
                    src={bot.image_url || undefined}
                    size="chat"
                    aria-hidden
                  />
                  <Stack gap="none" className="min-w-0 flex-1">
                    <Box
                      className="truncate text-label font-medium text-ink"
                      title={bot.name}
                    >
                      {bot.name}
                    </Box>
                    <Box className="text-meta text-ink-subtle">{bot.bot_id}</Box>
                  </Stack>
                  <Badge tone="positive" dot>
                    Enabled
                  </Badge>
                  {onBotChange && (
                    <Icon
                      icon={ChevronRight}
                      size="sm"
                      className="text-ink-subtle"
                    />
                  )}
                </button>
                <Button
                  size="compact"
                  variant="ghost"
                  aria-label={`Remove ${bot.name}`}
                  disabled={pending}
                  onClick={() => {
                    add.reset()
                    remove.mutate(bot)
                  }}
                >
                  Remove
                </Button>
              </Inline>
            ))}
          </Stack>
        )}
        <Inline gap="md" justify="between" wrap className={HELP_CLASS}>
          <span>
            {bots.data
              ? `${bots.data.length} ${bots.data.length === 1 ? "bot" : "bots"} allowed`
              : ""}
          </span>
          <Inline render={<span />} gap="xs">
            <Icon icon={Bot} size="sm" />
            Runs as Open SWE
          </Inline>
        </Inline>
        <Box render={<p />} className={HELP_CLASS}>
          Uses the Open SWE GitHub App’s repository access. Bots can only
          continue tasks they started.
        </Box>
      </Stack>
    </PageSection>
  )
}
