import { Link } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { Avatar } from "@langchain/macaw-components/Avatar"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { ArrowLeftIcon } from "@phosphor-icons/react/dist/ssr/ArrowLeft"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { CaretRightIcon } from "@phosphor-icons/react/dist/ssr/CaretRight"
import { RobotIcon } from "@phosphor-icons/react/dist/ssr/Robot"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"

import type { AgentStatus, AgentThread } from "@/features/agents/lib/types"
import type { AllowedSlackBotEntry } from "@/lib/api"
import { useThreadsPage } from "@/features/agents/lib/queries"
import { AllowedSlackBotsSection } from "@/features/bots/components/AllowedSlackBotsSection"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn, formatRelativeTime } from "@/lib/utils"

const THREAD_LIMIT = 50

const STATUS_LABELS: Record<AgentStatus, string> = {
  running: "Running",
  finished: "Finished",
  interrupted: "Interrupted",
  error: "Error",
  idle: "Idle",
}

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
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-6 py-8 max-md:pt-16">
        {bot && (
          <Button
            color="secondary"
            variant="underlined"
            leftDecorator={ArrowLeftIcon}
            className="mb-space-3"
            onClick={() => onBotChange(undefined)}
          >
            Bots
          </Button>
        )}
        <h1 className="text-base font-medium text-primary">
          {bot ? `${botsByKey.get(bot)?.name ?? "Bot"} threads` : "Bots"}
        </h1>
        <p className="mt-1 text-xs text-secondary">
          Threads started by Slack bots on the allowlist. They are read-only
          here: reply in the Slack thread to steer one.
          {!isAdmin && " Workspace admins manage which bots are allowed."}
        </p>

        {!bot &&
          (isAdmin ? (
            <div className="mt-4 rounded-xl border border-default bg-surface-level-1">
              <AllowedSlackBotsSection onBotChange={onBotChange} />
            </div>
          ) : (
            <div className="mt-4 divide-y divide-default rounded-xl border border-default bg-surface-level-1">
              {directory.isPending ? (
                <p className="p-4 text-xs text-secondary">
                  Loading enabled bots…
                </p>
              ) : directory.isError ? (
                <Banner
                  intent="error"
                  flush
                  action={
                    <Button
                      color="secondary"
                      variant="outlined"
                      onClick={() => void directory.refetch()}
                    >
                      Retry
                    </Button>
                  }
                >
                  Enabled bots could not be loaded.
                </Banner>
              ) : bots.length === 0 ? (
                <EmptyState
                  size="sm"
                  icon={RobotIcon}
                  title="No Slack bots are enabled."
                />
              ) : (
                bots.map((entry) => (
                  <button
                    key={entry.key}
                    type="button"
                    onClick={() => onBotChange(entry.key)}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-surface-level-1-hover focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none"
                  >
                    <BotAvatar entry={entry} />
                    <span className="min-w-0 flex-1 truncate text-sm font-medium text-primary">
                      {entry.name}
                    </span>
                    <span className="text-xs text-success-secondary">
                      Enabled
                    </span>
                    <CaretRightIcon
                      aria-hidden="true"
                      size={14}
                      weight="regular"
                      className="text-icon-secondary"
                    />
                  </button>
                ))
              )}
            </div>
          ))}

        {bot && (
          <div className="mt-6">
            {threadsQuery.isLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-16 w-full rounded-xl" />
                <Skeleton className="h-16 w-full rounded-xl" />
              </div>
            ) : threadsQuery.isError ? (
              <Banner
                intent="error"
                action={
                  <Button
                    color="secondary"
                    variant="outlined"
                    onClick={() => void threadsQuery.refetch()}
                    disabled={threadsQuery.isFetching}
                  >
                    {threadsQuery.isFetching ? "Retrying…" : "Retry"}
                  </Button>
                }
              >
                Bot threads could not be loaded.
              </Banner>
            ) : threads.length === 0 ? (
              <EmptyState
                icon={RobotIcon}
                title="No bot threads yet"
                description="They appear here once an allowed bot mentions Open SWE in Slack."
                className="rounded-xl border border-dashed border-default"
              />
            ) : (
              <div className="space-y-2">
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
                {threadsQuery.data?.hasMore && (
                  <p className="pt-4 text-center text-xs text-secondary">
                    Showing the {THREAD_LIMIT} most recent threads.
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function BotAvatar({ entry }: { entry: AllowedSlackBotEntry }) {
  return (
    <span aria-hidden="true" className="flex">
      <Avatar
        size="xs"
        label={entry.name}
        imageUrl={entry.image_url || undefined}
      />
    </span>
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
    <div className="flex items-center gap-3 rounded-xl border border-default bg-surface-level-1 px-4 py-3 transition-colors hover:bg-surface-level-1-hover">
      <Link
        to="/agents/$threadId"
        params={{ threadId: thread.id }}
        className="flex min-w-0 flex-1 items-center gap-3"
      >
        {thread.status === "running" ? (
          <Spinner size="xs" className="shrink-0 text-icon-brand" />
        ) : (
          <span
            className={cn(
              "size-2.5 shrink-0 rounded-full",
              thread.status === "error" || thread.status === "interrupted"
                ? "bg-error-strong"
                : thread.status === "finished"
                  ? "bg-success-strong"
                  : "bg-surface-level-4"
            )}
          />
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-primary">
            {thread.title}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-tertiary">
            <span>{STATUS_LABELS[thread.status]}</span>
            {thread.triggeringBot && (
              <span className="flex items-center gap-1">
                {entry && <BotAvatar entry={entry} />}
                {entry?.name ?? thread.triggeringBot.name}
              </span>
            )}
            {thread.repoFullName && <span>{thread.repoFullName}</span>}
            <span>{formatRelativeTime(thread.updatedAt)}</span>
          </div>
        </div>
      </Link>
      {slackUrl ? (
        <Button
          as={<a href={slackUrl} target="_blank" rel="noreferrer" />}
          color="secondary"
          variant="plain"
          leftDecorator={SlackLogoIcon}
          aria-label="Open the Slack thread"
        >
          Slack
        </Button>
      ) : (
        <ArrowSquareOutIcon
          size={16}
          weight="regular"
          className="shrink-0 text-icon-tertiary"
        />
      )}
    </div>
  )
}
