import { Link } from "@tanstack/react-router"
import { useMutation, useQuery } from "@tanstack/react-query"
import { useEffect, useMemo, useState } from "react"
import type { ReactNode } from "react"

import type { AdminUser } from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { TablePagination } from "@/components/TablePagination"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Switch } from "@langchain/gtm-platform-design-system/ui/switch"
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
          <span className="text-body font-medium text-ink">
            App manifest
          </span>
          <span className="text-meta text-ink-subtle">
            {placeholdersRemain
              ? "Copy the selected manifest, replace its remaining <…> placeholders, then paste it into your Slack app settings and reinstall the app."
              : "Copy the selected manifest — its URLs are filled in from this deployment — then paste it into your Slack app settings and reinstall the app."}
          </span>
        </div>
        <Button size="compact" variant="outline" onClick={() => void copyManifest()}>
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
          <span className="text-meta text-ink-subtle">
            {running.length} running
          </span>
          <Button
            size="compact"
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
                className="flex items-center justify-between gap-3 border-b border-line py-2 last:border-b-0"
              >
                <Link
                  to="/agents/$threadId"
                  params={{ threadId: thread.id }}
                  className="min-w-0 flex-1 hover:underline"
                >
                  <p className="truncate text-label font-medium text-ink">
                    {thread.title}
                  </p>
                  <p className="truncate font-mono text-meta text-ink-subtle">
                    {thread.repoFullName || "no repo"} · {thread.id}
                  </p>
                </Link>
                <Button
                  size="compact"
                  variant="destructive"
                  onClick={() => kill(thread)}
                >
                  Kill
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-meta text-ink-subtle">No running agents.</p>
        )}

        {threads.error && (
          <p className="text-label text-risk">{threads.error.message}</p>
        )}
        {message && <p className="text-meta text-ink-subtle">{message}</p>}
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
            size="compact"
            onClick={() => trigger.mutate()}
            disabled={!parsed || trigger.isPending}
          >
            {trigger.isPending ? "Starting…" : "Start review"}
          </Button>
        </div>
        {url.trim() && !parsed && (
          <p className="text-meta text-ink-subtle">
            Enter a full PR URL like https://github.com/owner/repo/pull/123
          </p>
        )}
        {message && parsed && (
          <p className="text-meta text-ink-subtle">
            {message}{" "}
            <Link
              to="/agents/reviews/$owner/$repo/$number"
              params={{
                owner: parsed.owner,
                repo: parsed.repo,
                number: String(parsed.number),
              }}
              className="underline hover:text-ink"
            >
              View review
            </Link>
          </p>
        )}
        {error && <p className="text-label text-risk">{error}</p>}
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
            <p role="alert" className="text-label text-risk">
              Could not load users. Please try again.
            </p>
          ) : !items.length ? (
            <p className="text-meta text-ink-subtle">
              {query ? "No users match your search." : "No users yet."}
            </p>
          ) : (
            items.map((user: AdminUser) => (
              <div
                key={user.user_id}
                className="flex items-center justify-between gap-2 border-b border-line py-1.5 text-label last:border-b-0"
              >
                <div className="flex min-w-0 flex-col">
                  <span className="truncate font-medium">
                    {user.github_login || user.display_name || user.user_id}
                  </span>
                  <span className="truncate text-meta text-ink-subtle">
                    {user.email}
                    {user.slack_user_id ? ` · Slack ${user.slack_user_id}` : ""}
                  </span>
                </div>
                {user.is_admin && (
                  <span className="text-meta font-medium text-ink-subtle">
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
