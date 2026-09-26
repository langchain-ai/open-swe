import { Link, Navigate, createFileRoute } from "@tanstack/react-router"
import { useMutation, useQuery } from "@tanstack/react-query"
import { CaretRightIcon } from "@phosphor-icons/react"
import { useEffect, useMemo, useState } from "react"
import type { ReactNode } from "react"

import type { AdminUser } from "@/lib/api"
import {
  AppShell,
  SettingsNavRow,
  SettingsRow,
  SettingsSection,
} from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { api } from "@/lib/api"
import {
  useAdminCancelAgentThread,
  useThreadsPage,
} from "@/features/agents/lib/queries"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"
import {
  slackAppManifestJson,
  slackManifestPlaceholdersRemain,
} from "@/lib/slack-manifest"
import { dashboardApiBase } from "@/lib/api-base"
import { AllowedSlackBotsSection } from "@/features/settings/components/AllowedSlackBotsSection"
import { ExpeditedReviewSection } from "@/features/settings/components/ExpeditedReviewSection"
import { LeaderboardPrivacySection } from "@/features/settings/components/LeaderboardPrivacySection"
import { MCPConnectionsSection } from "@/features/settings/components/MCPConnectionsSection"
import { ReviewSettings } from "@/features/settings/components/ReviewSettings"
import {
  DefaultRepoSection,
  FableSection,
  LLMGatewaySection,
  ModelDefaultsSection,
} from "@/features/settings/components/WorkspaceSettingsSections"
import { INSTANCE_SCOPE } from "@/features/settings/lib/settingsScope"
import { useOptions, useRepos } from "@/lib/profile"
import { IncidentSettings } from "@/features/incidents/IncidentSettings"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/admin")({
  head: () => ({ meta: [{ title: pageTitle("Admin") }] }),
  component: AdminPage,
})

function AdminPage() {
  const session = useSession()
  const modelOptions = useOptions()
  const repos = useRepos()

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session.data) return <RequireLogin />
  if (!session.data.is_admin) return <Navigate to="/my-settings" />

  return (
    <AppShell
      user={session.data}
      title="Admin"
      description="Defaults every workspace inherits, plus instance-wide integrations and user mappings. Open a workspace to override a setting there."
    >
      <SettingsSection title="Workspaces">
        <SettingsNavRow
          to="/workspaces"
          label="Workspace settings"
          description="Repositories, Slack channels, sandbox image, and overrides of the defaults below, per workspace."
        />
      </SettingsSection>

      <ModelDefaultsSection
        scope={INSTANCE_SCOPE}
        models={(modelOptions.data?.models ?? []).filter(
          (model) => model.can_be_default !== false
        )}
      />
      <DefaultRepoSection
        scope={INSTANCE_SCOPE}
        repositories={(repos.data?.repositories ?? []).map(
          (repo) => repo.full_name
        )}
      />
      <LLMGatewaySection scope={INSTANCE_SCOPE} />
      <FableSection scope={INSTANCE_SCOPE} />
      <ReviewSettings scope={INSTANCE_SCOPE} canEdit />
      <ExpeditedReviewSection scope={INSTANCE_SCOPE} />
      <MCPConnectionsSection scope="instance" />

      <SlackIntegrationSection
        backendUrl={session.data.slack_base_url ?? session.data.api_base_url}
      >
        <AllowedSlackBotsSection />
      </SlackIntegrationSection>

      <LeaderboardPrivacySection />

      <TriggerReviewSection />

      <div id="incidents" className="scroll-mt-8">
        <IncidentSettings />
      </div>

      <RunningAgentsSection />

      <SettingsSection title="Evals">
        <Link
          to="/admin/evals"
          className="flex items-center justify-between gap-6 px-4 py-3 hover:bg-muted/40"
        >
          <div className="flex flex-col gap-0.5">
            <span className="text-xs font-medium text-foreground">
              Reviewer eval
            </span>
            <span className="text-xs text-muted-foreground">
              Run the offline reviewer benchmark and watch its output stream
              live.
            </span>
          </div>
          <CaretRightIcon className="size-3.5 shrink-0 text-muted-foreground" />
        </Link>
      </SettingsSection>

      <UsersSection enabled={!!session.data.is_admin} />
    </AppShell>
  )
}

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
          <span className="text-sm/none font-medium text-foreground">
            App manifest
          </span>
          <span className="text-xs/relaxed text-muted-foreground">
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
          <span className="text-xs text-muted-foreground">
            {running.length} running
          </span>
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
                className="flex items-center justify-between gap-3 border-b border-border py-2 last:border-b-0"
              >
                <Link
                  to="/agents/$threadId"
                  params={{ threadId: thread.id }}
                  className="min-w-0 flex-1 hover:underline"
                >
                  <p className="truncate text-xs font-medium text-foreground">
                    {thread.title}
                  </p>
                  <p className="truncate font-mono text-[11px] text-muted-foreground">
                    {thread.repoFullName || "no repo"} · {thread.id}
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
          <p className="text-xs text-muted-foreground">No running agents.</p>
        )}

        {threads.error && (
          <p className="text-xs text-destructive">{threads.error.message}</p>
        )}
        {message && <p className="text-xs text-muted-foreground">{message}</p>}
      </div>
    </SettingsSection>
  )
}

const PR_URL_RE = /^https:\/\/github\.com\/([^/\s]+)\/([^/\s]+)\/pull\/(\d+)/

function TriggerReviewSection() {
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
          <p className="text-xs text-muted-foreground">
            Enter a full PR URL like https://github.com/owner/repo/pull/123
          </p>
        )}
        {message && parsed && (
          <p className="text-xs text-muted-foreground">
            {message}{" "}
            <Link
              to="/agents/reviews/$owner/$repo/$number"
              params={{
                owner: parsed.owner,
                repo: parsed.repo,
                number: String(parsed.number),
              }}
              className="underline hover:text-foreground"
            >
              View review
            </Link>
          </p>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}
      </div>
    </SettingsSection>
  )
}

const PAGE_SIZE = 20

function UsersSection({ enabled }: { enabled: boolean }) {
  const [page, setPage] = useState(1)

  const users = useQuery({
    queryKey: ["adminUsers", page],
    queryFn: () => api.adminListUsers(page, PAGE_SIZE),
    enabled,
  })

  const total = users.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const items = users.data?.items ?? []

  return (
    <SettingsSection
      title="Users"
      description="Everyone who has signed in with GitHub, and the Slack account each has connected from their own settings."
    >
      <div className="flex flex-col gap-3 p-4">
        <div className="flex flex-col gap-0.5">
          {users.isLoading ? (
            <Skeleton className="h-32" />
          ) : !items.length ? (
            <p className="text-xs text-muted-foreground">No users yet.</p>
          ) : (
            items.map((user: AdminUser) => (
              <div
                key={user.user_id}
                className="flex items-center justify-between gap-2 border-b border-border py-1.5 text-xs last:border-b-0"
              >
                <div className="flex min-w-0 flex-col">
                  <span className="truncate font-medium">
                    {user.github_login || user.display_name || user.user_id}
                  </span>
                  <span className="truncate text-xs text-muted-foreground">
                    {user.email}
                    {user.slack_user_id ? ` · Slack ${user.slack_user_id}` : ""}
                  </span>
                </div>
                {user.is_admin && (
                  <span className="text-[10px] font-medium text-muted-foreground">
                    Admin
                  </span>
                )}
              </div>
            ))
          )}
        </div>

        {total > PAGE_SIZE && (
          <div className="flex items-center justify-between pt-1 text-xs text-muted-foreground">
            <span>
              {total} user{total === 1 ? "" : "s"} · page {page} of {pageCount}
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1 || users.isFetching}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                disabled={page >= pageCount || users.isFetching}
              >
                Next
              </Button>
            </div>
          </div>
        )}
      </div>
    </SettingsSection>
  )
}
