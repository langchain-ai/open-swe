import { Link } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { PageMasthead } from "@langchain/gtm-platform-design-system/patterns/page-masthead"
import { RecordHop } from "@langchain/gtm-platform-design-system/patterns/record-header"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Avatar } from "@langchain/gtm-platform-design-system/ui/avatar"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import {
  Button,
  buttonVariants,
} from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { ProviderLogo } from "@langchain/gtm-platform-design-system/ui/provider-logos"
import { ScrollArea } from "@langchain/gtm-platform-design-system/ui/scroll-area"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"

import {
  AlertTriangle,
  Bot,
  ChevronRight,
  ExternalLink,
} from "@/components/glyphs"
import type { AgentThread } from "@/features/agents/lib/types"
import type { AllowedSlackBotEntry } from "@/lib/api"
import { useThreadsPage } from "@/features/agents/lib/queries"
import { RunStatusBadge } from "@/features/automations/components/AutomationRuns"
import { AllowedSlackBotsSection } from "@/features/bots/components/AllowedSlackBotsSection"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { formatRelativeTime } from "@/lib/utils"

const THREAD_LIMIT = 50

const PANEL_LIST_CLASS = "overflow-hidden"
const ROW_CLASS =
  "flex min-h-row-record w-full min-w-0 items-center gap-3 px-3 py-2 text-left outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset"

export function BotThreadsList({
  bot,
  onBotChange,
}: {
  bot?: string
  onBotChange: (bot: string | undefined) => void
}) {
  const directory = useQuery({
    queryKey: ["allowedSlackBotDirectory"],
    queryFn: api.listAllowedSlackBotDirectory,
    staleTime: 5 * 60 * 1000,
  })
  const bots = directory.data ?? []
  const threadsQuery = useThreadsPage(
    { limit: THREAD_LIMIT, offset: 0, scope: "bot", bot },
    { enabled: !!bot, pollWhileRunning: !!bot }
  )
  const threads = threadsQuery.data?.items ?? []
  const botsByKey = new Map(bots.map((entry) => [entry.key, entry]))
  const isAdmin = useSession().data?.is_admin === true

  return (
    <ScrollArea overflow="vertical" className="h-full min-h-0 min-w-0 flex-1">
      <Stack
        gap="xl"
        className="mx-auto w-full max-w-work px-6 py-6 max-md:pt-16"
      >
        <Stack gap="sm">
          {bot && (
            <Inline>
              <RecordHop label="Bots" onClick={() => onBotChange(undefined)} />
            </Inline>
          )}
          <PageMasthead
            title={
              bot ? `${botsByKey.get(bot)?.name ?? "Bot"} threads` : "Bots"
            }
            description={`Threads started by Slack bots on the allowlist. They are read-only here: reply in the Slack thread to steer one.${isAdmin ? "" : " Workspace admins manage which bots are allowed."}`}
          />
        </Stack>

        {!bot &&
          (isAdmin ? (
            <AllowedSlackBotsSection onBotChange={onBotChange} />
          ) : directory.isPending ? (
            <RowsSkeleton label="Loading enabled bots" />
          ) : directory.isError ? (
            <StateNotice
              tone="RISK"
              icon={AlertTriangle}
              title="Enabled bots could not be loaded."
              description={directory.error.message}
              action={
                <Button
                  variant="outline"
                  size="compact"
                  onClick={() => void directory.refetch()}
                >
                  Retry
                </Button>
              }
            />
          ) : bots.length === 0 ? (
            <EmptyState icon={Bot} title="No Slack bots are enabled." />
          ) : (
            <Stack
              render={<ul aria-label="Enabled bots" />}
              gap="none"
              bg="panel"
              border="line"
              radius="panel"
              className={PANEL_LIST_CLASS}
            >
              {bots.map((entry) => (
                <Box
                  key={entry.key}
                  render={<li />}
                  className="border-b border-line last:border-b-0"
                >
                  <button
                    type="button"
                    onClick={() => onBotChange(entry.key)}
                    className={ROW_CLASS}
                  >
                    <BotAvatar entry={entry} />
                    <Box className="min-w-0 flex-1 truncate text-label font-medium text-ink">
                      {entry.name}
                    </Box>
                    <Badge tone="positive" dot>
                      Enabled
                    </Badge>
                    <Icon
                      icon={ChevronRight}
                      size="sm"
                      className="text-ink-subtle"
                    />
                  </button>
                </Box>
              ))}
            </Stack>
          ))}

        {bot &&
          (threadsQuery.isLoading ? (
            <RowsSkeleton label="Loading bot threads" />
          ) : threadsQuery.isError ? (
            <StateNotice
              tone="RISK"
              icon={AlertTriangle}
              title="Bot threads could not be loaded."
              description="The bot's threads in Slack are unaffected."
              action={
                <Button
                  type="button"
                  size="compact"
                  variant="outline"
                  onClick={() => void threadsQuery.refetch()}
                  disabled={threadsQuery.isFetching}
                >
                  {threadsQuery.isFetching ? "Retrying…" : "Retry"}
                </Button>
              }
            />
          ) : threads.length === 0 ? (
            <EmptyState
              icon={Bot}
              title="No bot threads yet"
              description="They appear here once an allowed bot mentions Open SWE in Slack."
            />
          ) : (
            <Stack gap="md">
              <Stack
                render={<ul aria-label="Bot threads" />}
                gap="none"
                bg="panel"
                border="line"
                radius="panel"
                className={PANEL_LIST_CLASS}
              >
                {threads.map((thread) => (
                  <BotThreadRow
                    key={thread.id}
                    thread={thread}
                    entry={
                      thread.triggeringBot
                        ? botsByKey.get(thread.triggeringBot.key)
                        : undefined
                    }
                  />
                ))}
              </Stack>
              {threadsQuery.data?.hasMore && (
                <Box
                  render={<p />}
                  className="text-center text-meta text-ink-subtle"
                >
                  Showing the {THREAD_LIMIT} most recent threads.
                </Box>
              )}
            </Stack>
          ))}
      </Stack>
    </ScrollArea>
  )
}

function BotAvatar({ entry }: { entry: AllowedSlackBotEntry }) {
  return (
    <Avatar
      name={entry.name}
      src={entry.image_url || undefined}
      size="chat"
      aria-hidden
    />
  )
}

function BotThreadRow({
  thread,
  entry,
}: {
  thread: AgentThread
  entry?: AllowedSlackBotEntry
}) {
  const slackUrl = thread.sourceAppUrl ?? thread.sourceUrl
  return (
    <Inline
      render={<li />}
      gap="sm"
      className="border-b border-line pr-3 last:border-b-0 hover:bg-hover"
    >
      <Link
        to="/agents/$threadId"
        params={{ threadId: thread.id }}
        className={ROW_CLASS}
      >
        <Stack gap="none" className="min-w-0 flex-1">
          <Box
            render={<span />}
            className="truncate text-label font-medium text-ink"
          >
            {thread.title}
          </Box>
          <Inline
            render={<span />}
            gap="md"
            wrap
            className="text-meta text-ink-subtle"
          >
            {thread.triggeringBot && (
              <Inline render={<span />} gap="xs">
                <Icon icon={Bot} size="sm" />
                {entry?.name ?? thread.triggeringBot.name}
              </Inline>
            )}
            {thread.repoFullName && <span>{thread.repoFullName}</span>}
          </Inline>
        </Stack>
        <Inline render={<span />} justify="end" className="w-27.5 shrink-0">
          <RunStatusBadge status={thread.status} />
        </Inline>
        <Box
          render={<span />}
          className="w-20 shrink-0 text-right text-meta text-ink-subtle tabular-nums"
        >
          {formatRelativeTime(thread.updatedAt)}
        </Box>
      </Link>
      {slackUrl ? (
        <a
          href={slackUrl}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Open the Slack thread"
          className={buttonVariants({ variant: "ghost", size: "compact" })}
        >
          <ProviderLogo provider="slack" className="size-3.5" />
          Slack
        </a>
      ) : (
        <Icon icon={ExternalLink} size="sm" className="text-ink-subtle" />
      )}
    </Inline>
  )
}

function RowsSkeleton({ label }: { label: string }) {
  return (
    <Stack
      gap="none"
      bg="panel"
      border="line"
      radius="panel"
      aria-busy
      aria-label={label}
      className={PANEL_LIST_CLASS}
    >
      {[0, 1].map((row) => (
        <Inline
          key={row}
          gap="md"
          className="min-h-row-record border-b border-line px-3 py-2 last:border-b-0"
        >
          <Stack gap="xs" className="min-w-0 flex-1">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-3 w-32" />
          </Stack>
          <Skeleton className="h-5 w-16 rounded-badge" />
        </Inline>
      ))}
    </Stack>
  )
}
