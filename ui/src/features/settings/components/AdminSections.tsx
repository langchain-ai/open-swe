import { Link } from "@tanstack/react-router"
import { useMutation, useQuery } from "@tanstack/react-query"
import { useEffect, useMemo, useState } from "react"
import type { ReactNode } from "react"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { FormField } from "@langchain/gtm-platform-design-system/patterns/form-field"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import {
  SettingRow,
  SettingSection,
} from "@langchain/gtm-platform-design-system/patterns/setting-section"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Avatar } from "@langchain/gtm-platform-design-system/ui/avatar"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { SearchInput } from "@langchain/gtm-platform-design-system/ui/search-input"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Switch } from "@langchain/gtm-platform-design-system/ui/switch"

import { AlertTriangle, Bot, Users } from "@/components/glyphs"
import { TablePagination } from "@/components/TablePagination"
import type { AdminUser } from "@/lib/api"
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

/** The bordered list body a section's rows sit in, with any notice kept above it. */
const LIST_PANEL = {
  gap: "none",
  bg: "panel",
  border: "line",
  radius: "panel",
  className: "overflow-hidden",
} as const

const LIST_ROW_CLASS = "border-b border-line px-5 py-3 last:border-b-0"

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
      // The button itself reports the failure; there is nothing else to retry.
      setCopyState("failed")
    }
  }

  return (
    <SettingSection
      title="Slack integration"
      description="Configure Slack and choose which bots can start Open SWE runs. Reinstall the Slack app after pasting a new manifest."
      contained
    >
      <SettingRow
        label="Slack Code Channels"
        density="compact"
        description={
          enabled
            ? "Early-access Code Channels manifest is selected."
            : "Legacy manifest: messages use app mentions and Slack threads."
        }
        control={(slot) => (
          <Switch
            id={slot.id}
            aria-describedby={slot.describedById}
            checked={enabled}
            onCheckedChange={setCodeChannelsEnabled}
          />
        )}
      />
      <SettingRow
        label="App manifest"
        description={
          placeholdersRemain
            ? "Replace its remaining <…> placeholders before pasting it into Slack."
            : "Its URLs are filled in from this deployment."
        }
        control={(slot) => (
          <Button
            aria-describedby={slot.describedById}
            size="compact"
            variant="outline"
            onClick={() => void copyManifest()}
          >
            {copyState === "copied"
              ? "Copied"
              : copyState === "failed"
                ? "Copy failed"
                : "Copy manifest"}
          </Button>
        )}
      />
      {children}
    </SettingSection>
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
    <PageSection
      title="Running agents"
      description="Workspace-wide active threads. Killing a thread interrupts its pending and running runs and keeps its history."
      actions={
        <>
          <Box
            render={<span />}
            className="text-meta text-ink-subtle tabular-nums"
          >
            {running.length} running
          </Box>
          <Button
            size="compact"
            variant="outline"
            onClick={() => void threads.refetch()}
            disabled={threads.isFetching}
          >
            {threads.isFetching ? "Refreshing…" : "Refresh"}
          </Button>
        </>
      }
    >
      {threads.error && (
        <StateNotice
          tone="RISK"
          icon={AlertTriangle}
          title="Running agents did not load"
          description={threads.error.message}
        />
      )}
      <Stack {...LIST_PANEL}>
        {threads.isLoading ? (
          <Box padding="lg">
            <Skeleton className="h-20 w-full" />
          </Box>
        ) : running.length ? (
          running.map((thread) => (
            <Inline
              key={thread.id}
              gap="md"
              align="center"
              justify="between"
              className={LIST_ROW_CLASS}
            >
              <Link
                to="/agents/$threadId"
                params={{ threadId: thread.id }}
                className="group min-w-0 flex-1"
              >
                <Box
                  render={<p />}
                  className="truncate text-label font-medium text-ink group-hover:underline"
                >
                  {thread.title}
                </Box>
                <Box
                  render={<p />}
                  className="truncate font-mono text-meta text-ink-subtle"
                >
                  {thread.repoFullName || "no repo"} · {thread.id}
                </Box>
              </Link>
              {/* Interrupting keeps history and can be re-run, so it stays one click. */}
              <Button
                size="compact"
                variant="outline"
                onClick={() => kill(thread)}
              >
                Kill
              </Button>
            </Inline>
          ))
        ) : (
          <EmptyState icon={Bot} title="No running agents" />
        )}
      </Stack>
      {message && (
        <Box render={<p role="status" />} className="text-meta text-ink-subtle">
          {message}
        </Box>
      )}
    </PageSection>
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
    <PageSection
      title="Trigger a review"
      description="Manually start an Open SWE Review run on a pull request. The repository must be enabled for review."
      contained
      inset="padded"
    >
      <Stack
        render={
          <form
            onSubmit={(event) => {
              event.preventDefault()
              if (parsed && !trigger.isPending) trigger.mutate()
            }}
          />
        }
        gap="lg"
      >
        <FormField
          label="Pull request URL"
          help="A full PR URL, like https://github.com/owner/repo/pull/123"
          error={error ?? undefined}
          control={
            <Input
              placeholder="https://github.com/owner/repo/pull/123"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value)
                setMessage(null)
                setError(null)
              }}
            />
          }
        />
        <Inline gap="md" align="center" wrap>
          <Button type="submit" disabled={!parsed || trigger.isPending}>
            {trigger.isPending ? "Starting…" : "Start review"}
          </Button>
          {message && parsed && (
            <Box
              render={<p role="status" />}
              className="text-meta text-ink-subtle"
            >
              {message}{" "}
              <Link
                to="/agents/reviews/$owner/$repo/$number"
                params={{
                  owner: parsed.owner,
                  repo: parsed.repo,
                  number: String(parsed.number),
                }}
                className="text-ink underline"
              >
                View review
              </Link>
            </Box>
          )}
        </Inline>
      </Stack>
    </PageSection>
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
    <Stack gap="md">
      <SearchInput
        label="Search users"
        placeholder="Search by name, GitHub login, email, or Slack ID…"
        value={search}
        onValueChange={(next) => {
          setSearch(next)
          setPage(1)
        }}
      />
      {users.isError && (
        <StateNotice
          tone="RISK"
          icon={AlertTriangle}
          title="Users did not load"
          description="Reload the page to try again."
        />
      )}
      <Stack {...LIST_PANEL}>
        {users.isLoading ? (
          <Box padding="lg">
            <Skeleton className="h-32 w-full" />
          </Box>
        ) : !items.length ? (
          users.isError ? null : (
            <EmptyState
              icon={Users}
              title={query ? "No users match your search." : "No users yet."}
            />
          )
        ) : (
          <Stack gap="none">
            {items.map((user: AdminUser) => {
              const name =
                user.github_login || user.display_name || user.user_id
              return (
                <Inline
                  key={user.user_id}
                  gap="md"
                  align="center"
                  className={LIST_ROW_CLASS}
                >
                  <Avatar name={name} size="control" />
                  <Stack gap="none" className="min-w-0 flex-1">
                    <Box
                      render={<span />}
                      className="truncate text-label font-medium text-ink"
                    >
                      {name}
                    </Box>
                    <Box
                      render={<span />}
                      className="truncate text-meta text-ink-subtle"
                    >
                      {user.email}
                      {user.slack_user_id
                        ? ` · Slack ${user.slack_user_id}`
                        : ""}
                    </Box>
                  </Stack>
                  {user.is_admin && (
                    <Badge tier="quiet" tone="info">
                      Admin
                    </Badge>
                  )}
                </Inline>
              )
            })}
          </Stack>
        )}
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
      </Stack>
    </Stack>
  )
}
