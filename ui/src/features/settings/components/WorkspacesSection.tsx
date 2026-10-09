import { useState, type ReactNode } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Badge } from "@langchain/macaw-components/Badge"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { CheckIcon } from "@phosphor-icons/react/dist/ssr/Check"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"

import { SettingsSection } from "@/components/AppShell"
import { useWorkspaceOptions } from "@/features/agents/lib/queries"
import {
  slackChannelHref,
  slackChannelLabel,
  useSlackChannelDirectory,
} from "@/lib/slack-channels"
import {
  Chips,
  EMPTY_DRAFT,
  githubRepoHref,
  WorkspaceEditor,
  type WorkspaceDraft,
} from "./WorkspaceEditor"
import {
  api,
  type WorkspaceCreate,
  type WorkspaceOption,
  type WorkspaceRefreshStatus,
  type WorkspaceRefreshStep,
} from "@/lib/api"
import { cn, formatRelativeTime } from "@/lib/utils"

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
  kind: WorkspaceOption["refresh_kind"],
  hasSnapshot: boolean
): string {
  if (status === "success" && kind === "update") return "Updated"
  if (status === "success" && kind === "full") return "Rebuilt"
  if (status === "refreshing" && kind === "update") return "Updating…"
  if (status === "refreshing" && kind === "full")
    return hasSnapshot ? "Rebuilding…" : "Building…"
  return REFRESH_LABEL[status]
}

const REFRESH_CLASS: Record<WorkspaceRefreshStatus, string> = {
  never: "text-secondary",
  refreshing: "text-secondary",
  success: "text-secondary",
  failed: "text-error-secondary",
}

function refreshedAt(timestamp: string | null | undefined): string | null {
  if (!timestamp) return null
  const parsed = Date.parse(timestamp)
  return Number.isNaN(parsed) ? null : formatRelativeTime(parsed)
}

function StepMark({ status }: { status: WorkspaceRefreshStep["status"] }) {
  if (status === "running") return <span aria-hidden="true">…</span>
  const Glyph = status === "success" ? CheckIcon : XIcon
  return (
    <Glyph
      size={10}
      weight="bold"
      aria-label={status === "success" ? "Succeeded" : "Failed"}
    />
  )
}

const STEP_CLASS: Record<WorkspaceRefreshStep["status"], string> = {
  running: "border-default text-primary",
  success: "border-default text-secondary",
  failed: "border-error text-error-secondary",
}

// A rebuild runs for minutes to an hour; which stage it reached is the only
// thing that separates slow from wedged while it is still going.
function RefreshSteps({ steps }: { steps: Array<WorkspaceRefreshStep> }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {steps.map((step) => (
        <span
          key={step.label}
          className={cn(
            "inline-flex items-center gap-space-1 rounded-full border px-space-2 py-0.5 text-xxs",
            STEP_CLASS[step.status]
          )}
        >
          <StepMark status={step.status} /> {step.label}
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
    <div
      data-workspace-row
      className="flex flex-col gap-space-2 px-space-4 py-3.5"
    >
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-8">
        <div className="flex flex-col gap-1">
          <span className="flex items-center gap-space-2 text-sm/none font-medium text-primary">
            {workspace.name}
            {isDefault && (
              <Badge color="secondary" size="xs">
                Default
              </Badge>
            )}
          </span>
          <span className="text-xs/relaxed text-secondary">{detail}</span>
        </div>
        <div className="flex items-center gap-3 sm:shrink-0">
          <span
            className={cn("text-xs", REFRESH_CLASS[status])}
            title={
              status !== "refreshing" && when && workspace.refresh_finished_at
                ? new Date(workspace.refresh_finished_at).toLocaleString(
                    undefined,
                    { timeZoneName: "short" }
                  )
                : undefined
            }
          >
            {refreshLabel(
              status,
              workspace.refresh_kind,
              workspace.has_snapshot
            )}
            {status !== "refreshing" && when ? ` ${when}` : ""}
          </span>
          {configure}
        </div>
      </div>
      <Chips values={workspace.repos} hrefFor={githubRepoHref} />
      <Chips
        values={workspace.slack_channel_ids}
        labelFor={channelLabel}
        hrefFor={slackChannelHref}
      />
      {steps.length > 0 && <RefreshSteps steps={steps} />}
      {workspace.refresh_error && (
        <p className="text-xs/relaxed text-error-secondary">
          {workspace.refresh_error}
        </p>
      )}
      {/* The API omits the log for non-admins; this guard is defence in depth
          for a `bash -x` trace that can carry expanded credentials. */}
      {isAdmin && log && (
        <details className="text-xs text-secondary">
          <summary className="cursor-pointer select-none">Refresh log</summary>
          <pre className="mt-space-2 max-h-64 overflow-auto rounded-md border border-default bg-surface-level-2 p-space-3 text-xxs leading-relaxed whitespace-pre-wrap">
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
  const workspaces = useWorkspaceOptions()
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
      if (createDraft.setupScript.trim())
        body.setup_script = createDraft.setupScript
      if (createDraft.updateScript.trim())
        body.update_script = createDraft.updateScript
      await api.createWorkspace(body)
      await qc.invalidateQueries({ queryKey: WORKSPACE_OPTIONS_KEY })
      setAdding(false)
      setCreateDraft(EMPTY_DRAFT)
    } catch (e) {
      void qc.invalidateQueries({ queryKey: WORKSPACE_OPTIONS_KEY })
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
        <div className="px-space-4 py-3.5">
          <Skeleton className="h-8 w-full" />
        </div>
      ) : workspaces.isError ? (
        <div className="px-space-4 py-3.5">
          <Banner intent="error" title="Could not load workspaces." />
        </div>
      ) : !options || options.workspaces.length === 0 ? (
        <p className="px-space-4 py-3.5 text-xs text-secondary">
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
            <div className="flex flex-wrap items-center gap-space-2 border-t border-default px-space-4 py-3.5">
              {createError && (
                <p role="alert" className="text-xs text-error-secondary">
                  {createError}
                </p>
              )}
              <div className="ml-auto flex gap-space-2">
                <Button
                  size="xs"
                  color="secondary"
                  variant="plain"
                  disabled={creating}
                  onClick={() => {
                    setAdding(false)
                    setCreateDraft(EMPTY_DRAFT)
                    setCreateError(null)
                  }}
                >
                  {JSON.stringify(createDraft) !== JSON.stringify(EMPTY_DRAFT)
                    ? "Cancel"
                    : "Close"}
                </Button>
                <Button
                  size="xs"
                  color="primary"
                  disabled={creating || !createDraft.name.trim()}
                  onClick={() => void create()}
                >
                  {creating ? "Creating…" : "Create workspace"}
                </Button>
              </div>
            </div>
          </div>
        ) : (
          <div className="px-space-4 py-3.5">
            <Button size="xs" color="primary" onClick={() => setAdding(true)}>
              Add workspace
            </Button>
          </div>
        ))}
    </SettingsSection>
  )
}
