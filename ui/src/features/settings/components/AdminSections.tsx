import { Link } from "@tanstack/react-router"
import { useMutation, useQuery } from "@tanstack/react-query"
import { useEffect, useMemo, useState } from "react"
import type { ReactNode } from "react"

import type { AdminUser } from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { TablePagination } from "@/components/TablePagination"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { api } from "@/lib/api"
import {
  useAdminCancelAgentThread,
  useThreadsPage,
} from "@/features/agents/lib/queries"
import {
  slackAppManifestJson,
  slackManifestPlaceholdersRemain,
} from "@/lib/slack-manifest"
import { dashboardApiBase } from "@/lib/api-base"

const SLACK_CODE_CHANNELS_STORAGE_KEY =
  "open-swe.admin.slack-code-channels-enabled"

export function SlackIntegrationSection({
  backendUrl,
  children,
}: {
  backendUrl?: string
  children?: ReactNode
}) {
  const [enabled, setEnabled] = useState(false)
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">(
    "idle"
  )

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    setEnabled(
      window.localStorage.getItem(SLACK_CODE_CHANNELS_STORAGE_KEY) === "true"
    )
  }, [])

  const setCodeChannelsEnabled = (next: boolean) => {
    setEnabled(next)
    setCopyState("idle")
    window.localStorage.setItem(SLACK_CODE_CHANNELS_STORAGE_KEY, String(next))
  }

  const manifestConfig = {
    backendUrl:
      backendUrl ||
      dashboardApiBase() ||
      (typeof window === "undefined" ? "" : window.location.origin),
  }
  const placeholdersRemain = slackManifestPlaceholdersRemain(manifestConfig)

  const copyManifest = async () => {
    try {
      await navigator.clipboard.writeText(
        slackAppManifestJson(enabled, manifestConfig)
      )
      setCopyState("copied")
    } catch {
      setCopyState("failed")
    }
  }

  return (
    <SettingsSection
      title="Slack integration"
      description="Configure Slack and choose which bots can start Open SWE runs."
    >
      <SettingsRow
        htmlFor="slack-code-channels"
        label="Slack Code Channels"
        description={
          enabled
            ? "Early-access Code Channels manifest selected. Reinstall or re-authorize the Slack app after updating its manifest."
            : "Legacy Slack manifest selected. Messages continue to use app mentions and Slack threads."
        }
        control={
          <Switch
            id="slack-code-channels"
            checked={enabled}
            onCheckedChange={setCodeChannelsEnabled}
          />
        }
      />
      <div className="flex flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:gap-8">
        <div className="flex flex-col gap-1">
          <span className="text-sm/none font-medium text-primary">
            App manifest
          </span>
          <span className="text-xs/relaxed text-secondary">
            {placeholdersRemain
              ? "Copy the selected manifest, replace its remaining <…> placeholders, then paste it into your Slack app settings and reinstall the app."
              : "Copy the selected manifest — its URLs are filled in from this deployment — then paste it into your Slack app settings and reinstall the app."}
          </span>
        </div>
        <Button size="sm" variant="outline" onClick={() => void copyManifest()}>
          {copyState === "copied"
            ? "Copied"
            : copyState === "failed"
              ? "Copy failed"
              : "Copy manifest"}
        </Button>
      </div>
      {children}
    </SettingsSection>
  )
}

export function RunningAgentsSection() {
  const threads = useThreadsPage({
    all: true,
    status: "running",
    limit: 50,
  })
  const cancel = useAdminCancelAgentThread()
  const [killed, setKilled] = useState<ReadonlySet<string>>(new Set())
  const [message, setMessage] = useState<string | null>(null)
  const running = threads.data?.items.filter((t) => !killed.has(t.id)) ?? []

  const kill = (thread: { id: string; title: string }) => {
    setMessage(null)
    setKilled((prev) => new Set(prev).add(thread.id))
    void cancel.mutateAsync(thread.id).then(
      () => setMessage(`Interruption requested for ${thread.title}.`),
      () =>
        setKilled((prev) => {
          const next = new Set(prev)
          next.delete(thread.id)
          return next
        })
    )
  }

  return (
    <SettingsSection
      title="Running agents"
      description="Workspace-wide active threads. Killing a thread requests interruption of all pending and running runs without deleting its history."
    >
      <div className="flex flex-col gap-3 p-4">
        <div className="flex items-center justify-between">
          {threads.isFetching ? (
            <Skeleton className="h-4 w-16" />
          ) : (
            <span className="text-xs text-secondary">
              {running.length} running
            </span>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={() => void threads.refetch()}
            disabled={threads.isFetching}
          >
            {threads.isFetching ? "Refreshing…" : "Refresh"}
          </Button>
        </div>

        {threads.isLoading ? (
          <Skeleton className="h-20" />
        ) : running.length ? (
          <div className="flex flex-col">
            {running.map((thread) => (
              <div
                key={thread.id}
                className="flex items-center justify-between gap-3 border-b border-default py-2 last:border-b-0"
              >
                <Link
                  to="/agents/$threadId"
                  params={{ threadId: thread.id }}
                  className="min-w-0 flex-1 hover:underline"
                >
                  <p className="truncate text-xs font-medium text-primary">
                    {thread.title}
                  </p>
                  <p className="truncate font-mono text-[11px] text-secondary">
                    {thread.repoFullName || "no repo"} · {thread.id}
                  </p>
                  <p className="text-[11px] text-secondary">
                    Thread started{" "}
                    <time dateTime={new Date(thread.createdAt).toISOString()}>
                      {new Date(thread.createdAt).toLocaleString(undefined, {
                        timeZoneName: "short",
                      })}
                    </time>
                  </p>
                </Link>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={() => kill(thread)}
                >
                  Kill
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-secondary">No running agents.</p>
        )}

        {threads.error && (
          <p className="text-xs text-error-secondary">{threads.error.message}</p>
        )}
        {message && <p className="text-xs text-secondary">{message}</p>}
      </div>
    </SettingsSection>
  )
}

const PR_URL_RE = /^https:\/\/github\.com\/([^/\s]+)\/([^/\s]+)\/pull\/(\d+)/

export function TriggerReviewSection() {
  const [url, setUrl] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const parsed = useMemo(() => {
    const match = PR_URL_RE.exec(url.trim())
    if (!match) return null
    const [, owner, repo, number] = match
    if (!owner || !repo || !number) return null
    return { owner, repo, number: Number(number) }
  }, [url])

  const trigger = useMutation({
    mutationFn: () => {
      if (!parsed) throw new Error("invalid PR URL")
      return api.reReview(parsed.owner, parsed.repo, parsed.number)
    },
    meta: { silent: true },
    onSuccess: (result) => {
      setError(null)
      setMessage(
        result.queued
          ? "Review queued — a run is already in progress on this PR."
          : "Review started."
      )
    },
    onError: (e: Error) => {
      setMessage(null)
      setError(e.message)
    },
  })

  return (
    <SettingsSection
      title="Trigger a review"
      description="Manually start an Open SWE Review run on a pull request. The repository must be enabled for review."
    >
      <div className="flex flex-col gap-2 p-4">
        <div className="flex items-center gap-2">
          <Input
            className="flex-1"
            placeholder="https://github.com/owner/repo/pull/123"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value)
              setMessage(null)
              setError(null)
            }}
          />
          <Button
            size="sm"
            onClick={() => trigger.mutate()}
            disabled={!parsed || trigger.isPending}
          >
            {trigger.isPending ? "Starting…" : "Start review"}
          </Button>
        </div>
        {url.trim() && !parsed && (
          <p className="text-xs text-secondary">
            Enter a full PR URL like https://github.com/owner/repo/pull/123
          </p>
        )}
        {message && parsed && (
          <p className="text-xs text-secondary">
            {message}{" "}
            <Link
              to="/agents/reviews/$owner/$repo/$number"
              params={{
                owner: parsed.owner,
                repo: parsed.repo,
                number: String(parsed.number),
              }}
              className="underline hover:text-primary"
            >
              View review
            </Link>
          </p>
        )}
        {error && <p className="text-xs text-error-secondary">{error}</p>}
      </div>
    </SettingsSection>
  )
}

export function UsersSection({ enabled }: { enabled: boolean }) {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [search, setSearch] = useState("")
  const query = search.trim()

  const users = useQuery({
    queryKey: ["adminUsers", page, pageSize, query],
    queryFn: () => api.adminListUsers(page, pageSize, query),
    enabled,
  })

  const total = users.data?.total ?? 0
  const items = users.data?.items ?? []

  return (
    <SettingsSection
      title="Users"
      description="Everyone who has signed in with GitHub, and the Slack account each has connected from their own settings."
    >
      <div className="flex flex-col gap-3 p-4">
        <Input
          aria-label="Search users"
          placeholder="Search by name, GitHub login, email, or Slack ID…"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value)
            setPage(1)
          }}
        />
        <div className="flex flex-col gap-0.5">
          {users.isLoading ? (
            <Skeleton className="h-32" />
          ) : users.isError ? (
            <p role="alert" className="text-xs text-error-secondary">
              Could not load users. Please try again.
            </p>
          ) : !items.length ? (
            <p className="text-xs text-secondary">
              {query ? "No users match your search." : "No users yet."}
            </p>
          ) : (
            items.map((user: AdminUser) => (
              <div
                key={user.user_id}
                className="flex items-center justify-between gap-2 border-b border-default py-1.5 text-xs last:border-b-0"
              >
                <Avatar>
                  <AvatarImage src={user.avatar_url} alt="" />
                  <AvatarFallback>
                    {(user.github_login || user.display_name || user.user_id)
                      .slice(0, 2)
                      .toUpperCase()}
                  </AvatarFallback>
                </Avatar>
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate font-medium">
                    {user.github_login || user.display_name || user.user_id}
                  </span>
                  <span className="truncate text-xs text-secondary">
                    {user.email}
                    {user.slack_user_id ? ` · Slack ${user.slack_user_id}` : ""}
                  </span>
                </div>
                {user.is_admin && (
                  <span className="text-[10px] font-medium text-secondary">
                    Admin
                  </span>
                )}
              </div>
            ))
          )}
        </div>
      </div>
      <TablePagination
        page={page}
        pageSize={pageSize}
        total={total}
        disabled={users.isFetching}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size)
          setPage(1)
        }}
      />
    </SettingsSection>
  )
}
