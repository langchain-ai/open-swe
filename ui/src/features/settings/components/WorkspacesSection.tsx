import { useState, type ReactNode } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { SettingsSection } from "@/components/AppShell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  slackChannelLabel,
  useSlackChannelDirectory,
} from "@/lib/slack-channels"
import {
  Chips,
  EMPTY_DRAFT,
  WorkspaceEditor,
  type ChipHref,
  type WorkspaceDraft,
} from "./WorkspaceEditor"
import {
  api,
  type WorkspaceCreate,
  type WorkspaceOption,
  type WorkspaceRefreshStatus,
  type WorkspaceRefreshStep,
} from "@/lib/api"
import { formatRelativeTime } from "@/lib/utils"

export const WORKSPACE_OPTIONS_KEY = ["workspace-options"]

const REFRESH_LABEL: Record<WorkspaceRefreshStatus, string> = {
  never: "Never refreshed",
  refreshing: "Refreshing…",
  success: "Refreshed",
  failed: "Refresh failed",
}

// A nightly rebuild from the base image and an hourly update of the current
// snapshot read differently to a person deciding whether to trust the image.
function refreshLabel(
  status: WorkspaceRefreshStatus,
  kind: WorkspaceOption["refresh_kind"]
): string {
  if (status === "success" && kind === "update") return "Updated"
  if (status === "success" && kind === "full") return "Rebuilt"
  if (status === "refreshing" && kind === "update") return "Updating…"
  if (status === "refreshing" && kind === "full") return "Rebuilding…"
  return REFRESH_LABEL[status]
}

const REFRESH_CLASS: Record<WorkspaceRefreshStatus, string> = {
  never: "text-muted-foreground",
  refreshing: "text-muted-foreground",
  success: "text-muted-foreground",
  failed: "text-destructive",
}

function refreshedAt(timestamp: string | null | undefined): string | null {
  if (!timestamp) return null
  const parsed = Date.parse(timestamp)
  return Number.isNaN(parsed) ? null : formatRelativeTime(parsed)
}

// Repositories are stored as `owner/name`; anything else (an unlinked Slack
// id, a future format) stays an inert chip.
const REPO_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/
const githubRepoHref: ChipHref = (repo) =>
  REPO_PATTERN.test(repo) ? `https://github.com/${repo}` : null

const STEP_MARK: Record<WorkspaceRefreshStep["status"], string> = {
  running: "…",
  success: "✓",
  failed: "✕",
}

const STEP_CLASS: Record<WorkspaceRefreshStep["status"], string> = {
  running: "border-border text-foreground",
  success: "border-border text-muted-foreground",
  failed: "border-destructive/40 text-destructive",
}

// A rebuild runs for minutes to an hour; which stage it reached is the only
// thing that separates slow from wedged while it is still going.
function RefreshSteps({ steps }: { steps: Array<WorkspaceRefreshStep> }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {steps.map((step) => (
        <span
          key={step.label}
          className={`rounded-full border px-2 py-0.5 text-[11px] ${STEP_CLASS[step.status]}`}
        >
          {STEP_MARK[step.status]} {step.label}
          {step.exit_code ? ` (exit ${step.exit_code})` : ""}
        </span>
      ))}
    </div>
  )
}
function WorkspaceRow({
  workspace,
  channelLabel,
  isDefault,
  isAdmin,
  configure,
}: {
  workspace: WorkspaceOption
  channelLabel: (id: string) => string
  isDefault: boolean
  isAdmin: boolean
  /** The control that opens this workspace's settings page. */
  configure?: ReactNode
}) {
  const status = workspace.refresh_status ?? "never"
  const when = refreshedAt(workspace.refresh_finished_at)
  const log = workspace.refresh_log_excerpt
  const steps = workspace.refresh_steps ?? []
  const detail = workspace.has_snapshot ? "Snapshot ready" : "No snapshot"

  return (
    <div data-workspace-row className="flex flex-col gap-2 px-4 py-3.5">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-8">
        <div className="flex flex-col gap-1">
          <span className="flex items-center gap-2 text-sm/none font-medium text-foreground">
            {workspace.name}
            {isDefault && <Badge variant="secondary">Default</Badge>}
          </span>
          <span className="text-xs/relaxed text-muted-foreground">
            {detail}
          </span>
        </div>
        <div className="flex items-center gap-3 sm:shrink-0">
          <span
            className={`text-xs ${REFRESH_CLASS[status]}`}
            title={
              status !== "refreshing" && when && workspace.refresh_finished_at
                ? new Date(workspace.refresh_finished_at).toLocaleString(
                    undefined,
                    { timeZoneName: "short" }
                  )
                : undefined
            }
          >
            {refreshLabel(status, workspace.refresh_kind)}
            {status !== "refreshing" && when ? ` ${when}` : ""}
          </span>
          {configure}
        </div>
      </div>
      <Chips values={workspace.repos} hrefFor={githubRepoHref} />
      <Chips values={workspace.slack_channel_ids.map(channelLabel)} />
      {steps.length > 0 && <RefreshSteps steps={steps} />}
      {workspace.refresh_error && (
        <p className="text-xs/relaxed text-destructive">
          {workspace.refresh_error}
        </p>
      )}
      {/* The API omits the log for non-admins; this guard is defence in depth
          for a `bash -x` trace that can carry expanded credentials. */}
      {isAdmin && log && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none">Refresh log</summary>
          <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-border bg-muted/40 p-3 text-[11px] leading-relaxed whitespace-pre-wrap">
            {log}
          </pre>
        </details>
      )}
    </div>
  )
}

export function WorkspacesSection({
  isAdmin,
  renderConfigure,
}: {
  isAdmin: boolean
  /** Renders the link to a workspace's settings page; admins only. */
  renderConfigure?: (workspace: WorkspaceOption) => ReactNode
}) {
  const qc = useQueryClient()
  const workspaces = useQuery({
    queryKey: WORKSPACE_OPTIONS_KEY,
    queryFn: api.listWorkspaceOptions,
    staleTime: 60_000,
    refetchInterval: 5000,
  })
  const options = workspaces.data
  // Channel names are an admin's view; everyone else sees the stored ids.
  const channelDirectory = useSlackChannelDirectory(isAdmin)
  const channelLabel = (id: string) =>
    slackChannelLabel(channelDirectory.data, id)

  const [adding, setAdding] = useState(false)
  const [createDraft, setCreateDraft] = useState<WorkspaceDraft>(EMPTY_DRAFT)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const create = async () => {
    setCreating(true)
    setCreateError(null)
    try {
      const body: WorkspaceCreate = {
        name: createDraft.name.trim(),
        repos: createDraft.repos,
        slack_channel_ids: createDraft.slackChannelIds,
        kitchen_channel_ids: createDraft.kitchenChannelIds,
      }
      const prompt = createDraft.prompt.trim()
      if (prompt) body.prompt = prompt
      await api.createWorkspace(body)
      await qc.invalidateQueries({ queryKey: WORKSPACE_OPTIONS_KEY })
      setAdding(false)
      setCreateDraft(EMPTY_DRAFT)
    } catch (e) {
      setCreateError(
        e instanceof Error ? e.message : "Could not create the workspace"
      )
    }
    setCreating(false)
  }

  return (
    <SettingsSection
      title="Workspaces"
      description={
        isAdmin
          ? "Each workspace is rebuilt nightly from its setup script and, while in use, updated hourly by its update script. Open a workspace to change its name, repositories, Slack channels, instructions, scripts, model defaults, review settings, and MCP connections. To publish a new sandbox image, start a new agent thread, open the + menu, enable admin mode, and ask Open SWE to capture it."
          : "Each workspace is rebuilt nightly from its setup script and, while in use, updated hourly by its update script. To create or edit one, ask a workspace admin to start an admin thread and ask Open SWE to make the change."
      }
    >
      {workspaces.isLoading ? (
        <div className="px-4 py-3.5">
          <Skeleton className="h-8 w-full" />
        </div>
      ) : workspaces.isError ? (
        <p className="px-4 py-3.5 text-xs text-destructive">
          Could not load workspaces.
        </p>
      ) : !options || options.workspaces.length === 0 ? (
        <p className="px-4 py-3.5 text-xs text-muted-foreground">
          No workspaces are configured.
        </p>
      ) : (
        options.workspaces.map((workspace) => (
          <WorkspaceRow
            key={workspace.slug}
            workspace={workspace}
            channelLabel={channelLabel}
            isDefault={workspace.slug === options.default_slug}
            isAdmin={isAdmin}
            configure={isAdmin ? renderConfigure?.(workspace) : null}
          />
        ))
      )}
      {isAdmin &&
        (adding ? (
          <div>
            <WorkspaceEditor
              draft={createDraft}
              onChange={setCreateDraft}
              promptHint="Instructions appended to every run in this workspace"
              workspaceSlug={null}
              workspaces={options?.workspaces ?? []}
              channelLabel={channelLabel}
            />
            <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3.5">
              {createError && (
                <p role="alert" className="text-xs text-destructive">
                  {createError}
                </p>
              )}
              <div className="ml-auto flex gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={creating}
                  onClick={() => {
                    setAdding(false)
                    setCreateDraft(EMPTY_DRAFT)
                    setCreateError(null)
                  }}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  disabled={creating || !createDraft.name.trim()}
                  onClick={() => void create()}
                >
                  {creating ? "Creating…" : "Create workspace"}
                </Button>
              </div>
            </div>
          </div>
        ) : (
          <div className="px-4 py-3.5">
            <Button size="sm" onClick={() => setAdding(true)}>
              Add workspace
            </Button>
          </div>
        ))}
    </SettingsSection>
  )
}
