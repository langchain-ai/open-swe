import { Link } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import {
  ArrowSquareOutIcon,
  CircleNotchIcon,
  RobotIcon,
} from "@phosphor-icons/react"
import { IoLogoSlack } from "react-icons/io5"

import type { AgentStatus, AgentThread } from "@/features/agents/lib/types"
import type { AllowedSlackBotEntry } from "@/lib/api"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
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
    { pollWhileRunning: true }
  )
  const threads = threadsQuery.data?.items ?? []
  const botsByKey = new Map(bots.map((entry) => [entry.key, entry]))
  const isAdmin = useSession().data?.is_admin === true

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-6 py-8 max-md:pt-16">
        <h1 className="text-base font-medium text-foreground">Bots</h1>
        <p className="mt-1 text-xs text-muted-foreground">
          Threads started by Slack bots on the allowlist. They are read-only
          here: reply in the Slack thread to steer one.
          {!isAdmin && " Workspace admins manage which bots are allowed."}
        </p>

        {isAdmin && (
          <div className="mt-4 rounded-xl border border-border bg-card">
            <AllowedSlackBotsSection />
          </div>
        )}

        {bots.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-1.5">
            <BotChip
              label="All bots"
              selected={!bot}
              onClick={() => onBotChange(undefined)}
            />
            {bots.map((entry) => (
              <BotChip
                key={entry.key}
                label={entry.name}
                entry={entry}
                selected={bot === entry.key}
                onClick={() => onBotChange(entry.key)}
              />
            ))}
          </div>
        )}

        <div className="mt-6">
          {threadsQuery.isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-16 w-full rounded-xl" />
              <Skeleton className="h-16 w-full rounded-xl" />
            </div>
          ) : threadsQuery.isError ? (
            <div className="flex flex-col items-center rounded-xl border border-destructive/30 bg-destructive/5 px-6 py-12 text-center">
              <p className="text-xs text-destructive">
                Bot threads could not be loaded.
              </p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="mt-3"
                onClick={() => void threadsQuery.refetch()}
                disabled={threadsQuery.isFetching}
              >
                {threadsQuery.isFetching ? "Retrying…" : "Retry"}
              </Button>
            </div>
          ) : threads.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border px-6 py-12 text-center text-xs text-muted-foreground">
              No bot threads yet. They appear here once an allowed bot mentions
              Open SWE in Slack.
            </div>
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
                <p className="pt-4 text-center text-xs text-muted-foreground">
                  Showing the {THREAD_LIMIT} most recent threads.
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function BotChip({
  label,
  entry,
  selected,
  onClick,
}: {
  label: string
  entry?: AllowedSlackBotEntry
  selected: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors",
        selected
          ? "border-foreground/30 bg-accent text-foreground"
          : "border-border bg-card text-muted-foreground hover:text-foreground"
      )}
    >
      {entry && <BotAvatar entry={entry} />}
      <span className="max-w-40 truncate">{label}</span>
    </button>
  )
}

function BotAvatar({ entry }: { entry: AllowedSlackBotEntry }) {
  return (
    <Avatar className="size-4">
      {entry.image_url && <AvatarImage src={entry.image_url} alt="" />}
      <AvatarFallback>
        <RobotIcon className="size-3" />
      </AvatarFallback>
    </Avatar>
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
    <div className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3 transition-colors hover:border-muted-foreground/70">
      <Link
        to="/agents/$threadId"
        params={{ threadId: thread.id }}
        className="flex min-w-0 flex-1 items-center gap-3"
      >
        {thread.status === "running" ? (
          <CircleNotchIcon className="size-4 shrink-0 animate-spin text-primary" />
        ) : (
          <span
            className={cn(
              "size-2.5 shrink-0 rounded-full",
              thread.status === "error" || thread.status === "interrupted"
                ? "bg-destructive"
                : thread.status === "finished"
                  ? "bg-success"
                  : "bg-border"
            )}
          />
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-foreground">
            {thread.title}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground/70">
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
        <a
          href={slackUrl}
          target="_blank"
          rel="noreferrer"
          className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label="Open the Slack thread"
        >
          <IoLogoSlack className="size-3.5" />
          Slack
        </a>
      ) : (
        <ArrowSquareOutIcon className="size-4 shrink-0 text-muted-foreground/70" />
      )}
    </div>
  )
}
