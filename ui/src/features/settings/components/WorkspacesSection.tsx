import { useState, type ReactNode } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { FormSection } from "@langchain/gtm-platform-design-system/patterns/form-field"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  Collapsible,
  CollapsibleChevron,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@langchain/gtm-platform-design-system/ui/collapsible"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { AlertTriangle, Layers } from "@/components/glyphs"
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
import { formatRelativeTime } from "@/lib/utils"

import { FormError } from "./WorkspaceSandboxSection"

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

const REFRESH_INK: Record<WorkspaceRefreshStatus, "ink-subtle" | "risk"> = {
  never: "ink-subtle",
  refreshing: "ink-subtle",
  success: "ink-subtle",
  failed: "risk",
}

function refreshedAt(timestamp: string | null | undefined): string | null {
  if (!timestamp) return null
  const parsed = Date.parse(timestamp)
  return Number.isNaN(parsed) ? null : formatRelativeTime(parsed)
}

const STEP_MARK: Record<WorkspaceRefreshStep["status"], string> = {
  running: "…",
  success: "✓",
  failed: "✕",
}

const STEP_TONE: Record<
  WorkspaceRefreshStep["status"],
  "info" | "neutral" | "risk"
> = {
  running: "info",
  success: "neutral",
  failed: "risk",
}

// A rebuild runs for minutes to an hour; which stage it reached is the only
// thing that separates slow from wedged while it is still going.
function RefreshSteps({ steps }: { steps: Array<WorkspaceRefreshStep> }) {
  return (
    <Inline gap="xs" wrap>
      {steps.map((step) => (
        <Badge key={step.label} tier="quiet" tone={STEP_TONE[step.status]}>
          {STEP_MARK[step.status]} {step.label}
          {step.exit_code ? ` (exit ${step.exit_code})` : ""}
        </Badge>
      ))}
    </Inline>
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
    <Stack
      data-workspace-row
      gap="sm"
      className="border-b border-line px-5 py-3 last:border-b-0"
    >
      <Inline gap="lg" align="center" justify="between" wrap>
        <Stack gap="xs" className="min-w-0">
          <Inline gap="sm" align="center">
            <Box render={<span />} className="text-label font-medium text-ink">
              {workspace.name}
            </Box>
            {isDefault && (
              <Badge tier="quiet" tone="info">
                Default
              </Badge>
            )}
          </Inline>
          <Box render={<span />} className="text-meta text-ink-subtle">
            {detail}
          </Box>
        </Stack>
        <Inline gap="md" align="center" className="shrink-0">
          <Box
            render={<span />}
            ink={REFRESH_INK[status]}
            className="text-label"
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
          </Box>
          {configure}
        </Inline>
      </Inline>
      <Chips values={workspace.repos} hrefFor={githubRepoHref} />
      <Chips
        values={workspace.slack_channel_ids}
        labelFor={channelLabel}
        hrefFor={slackChannelHref}
      />
      {steps.length > 0 && <RefreshSteps steps={steps} />}
      {workspace.refresh_error && (
        <Inline gap="sm" align="start" ink="risk" className="text-label">
          <Icon icon={AlertTriangle} size="sm" className="mt-0.5" />
          <Box render={<span />}>{workspace.refresh_error}</Box>
        </Inline>
      )}
      {/* The API omits the log for non-admins; this guard is defence in depth
          for a `bash -x` trace that can carry expanded credentials. */}
      {isAdmin && log && (
        <Collapsible>
          <CollapsibleTrigger className="text-meta text-ink-subtle">
            <CollapsibleChevron />
            Refresh log
          </CollapsibleTrigger>
          <CollapsibleContent>
            <Box
              render={<pre />}
              padding="md"
              bg="muted"
              radius="compact"
              border="line"
              className="mt-2 max-h-64 overflow-auto font-mono text-meta whitespace-pre-wrap text-ink-muted"
            >
              {log}
            </Box>
          </CollapsibleContent>
        </Collapsible>
      )}
    </Stack>
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

  const createDirty =
    JSON.stringify(createDraft) !== JSON.stringify(EMPTY_DRAFT)

  return (
    <PageSection
      contained
      title="Workspaces"
      description={
        isAdmin
          ? "Each workspace is rebuilt nightly from its setup script and, while in use, updated hourly by its update script. Open a workspace to change its name, repositories, Slack channels, instructions, scripts, model defaults, review settings, and MCP connections. To publish a new sandbox image, start a new agent thread, open the + menu, enable admin mode, and ask Open SWE to capture it."
          : "Each workspace is rebuilt nightly from its setup script and, while in use, updated hourly by its update script. To create or edit one, ask a workspace admin to start an admin thread and ask Open SWE to make the change."
      }
      actions={
        isAdmin && !adding ? (
          <Button size="compact" onClick={() => setAdding(true)}>
            Add workspace
          </Button>
        ) : undefined
      }
    >
      <Stack gap="none">
        {workspaces.isLoading ? (
          <Box padding="lg">
            <Skeleton className="h-row-record w-full" />
          </Box>
        ) : workspaces.isError ? (
          <Box padding="lg">
            <StateNotice
              tone="RISK"
              icon={AlertTriangle}
              title="Workspaces did not load"
              description="Could not load workspaces."
            />
          </Box>
        ) : !options || options.workspaces.length === 0 ? (
          <EmptyState icon={Layers} title="No workspaces are configured." />
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
        {isAdmin && adding && (
          <Stack gap="lg" className="border-t border-line px-5 py-4">
            <FormSection title="New workspace">
              <WorkspaceEditor
                draft={createDraft}
                onChange={setCreateDraft}
                promptHint="Instructions appended to every run in this workspace"
                workspaceSlug={null}
                workspaces={options?.workspaces ?? []}
                channelLabel={channelLabel}
              />
            </FormSection>
            {createError && <FormError message={createError} />}
            <Inline gap="sm" justify="end">
              <Button
                size="compact"
                variant="ghost"
                disabled={creating}
                onClick={() => {
                  setAdding(false)
                  setCreateDraft(EMPTY_DRAFT)
                  setCreateError(null)
                }}
              >
                {createDirty ? "Cancel" : "Close"}
              </Button>
              <Button
                size="compact"
                disabled={creating || !createDraft.name.trim()}
                onClick={() => void create()}
              >
                {creating ? "Creating…" : "Create workspace"}
              </Button>
            </Inline>
          </Stack>
        )}
      </Stack>
    </PageSection>
  )
}
