import { useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { SettingsSection } from "@/components/AppShell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import {
  RepositoryPicker,
  SlackChannelPicker,
  slackChannelLabel,
  useSlackChannelDirectory,
} from "./WorkspaceBindingPickers"
import {
  api,
  type WorkspaceCreate,
  type WorkspaceOption,
  type WorkspaceRecord,
  type WorkspaceRefreshStatus,
  type WorkspaceRefreshStep,
  type WorkspaceUpdate,
} from "@/lib/api"
import { formatRelativeTime } from "@/lib/utils"

const WORKSPACE_OPTIONS_KEY = ["workspace-options"]

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

function Chips({ values }: { values: Array<string> }) {
  if (values.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {values.map((value) => (
        <span
          key={value}
          className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground"
        >
          {value}
        </span>
      ))}
    </div>
  )
}

interface WorkspaceDraft {
  name: string
  repos: Array<string>
  slackChannelIds: Array<string>
  prompt: string
}

function draftFromWorkspace(workspace: WorkspaceRecord): WorkspaceDraft {
  return {
    name: workspace.name,
    repos: workspace.repos,
    slackChannelIds: workspace.slack_channel_ids,
    prompt: workspace.prompt,
  }
}

const EMPTY_DRAFT: WorkspaceDraft = {
  name: "",
  repos: [],
  slackChannelIds: [],
  prompt: "",
}

function WorkspaceEditor({
  draft,
  onChange,
  promptHint,
  workspaceSlug,
  workspaces,
  channelLabel,
}: {
  draft: WorkspaceDraft
  onChange: (next: WorkspaceDraft) => void
  promptHint: string
  /** The workspace being edited; null while creating one. */
  workspaceSlug: string | null
  workspaces: Array<WorkspaceOption>
  channelLabel: (id: string) => string
}) {
  return (
    <div className="space-y-3 border-t border-border px-4 py-3.5">
      <label className="block text-sm">
        Name
        <Input
          aria-label="Workspace name"
          value={draft.name}
          onChange={(e) => onChange({ ...draft, name: e.target.value })}
        />
      </label>
      <div className="text-sm">
        Repositories
        <div
          role="group"
          aria-label="Repositories"
          className="mt-1 flex flex-wrap items-center gap-2"
        >
          {draft.repos.length > 0 ? (
            <Chips values={draft.repos} />
          ) : (
            <span className="text-xs text-muted-foreground">None yet</span>
          )}
          <RepositoryPicker
            selected={draft.repos}
            onChange={(repos) => onChange({ ...draft, repos })}
            workspaceSlug={workspaceSlug}
            workspaces={workspaces}
          />
        </div>
      </div>
      <div className="text-sm">
        Slack channels
        <div
          role="group"
          aria-label="Slack channels"
          className="mt-1 flex flex-wrap items-center gap-2"
        >
          {draft.slackChannelIds.length > 0 ? (
            <Chips values={draft.slackChannelIds.map(channelLabel)} />
          ) : (
            <span className="text-xs text-muted-foreground">None yet</span>
          )}
          <SlackChannelPicker
            selected={draft.slackChannelIds}
            onChange={(slackChannelIds) =>
              onChange({ ...draft, slackChannelIds })
            }
            workspaceSlug={workspaceSlug}
            workspaces={workspaces}
          />
        </div>
      </div>
      <label className="block text-sm">
        Instructions
        <Textarea
          aria-label="Instructions"
          placeholder={promptHint}
          value={draft.prompt}
          onChange={(e) => onChange({ ...draft, prompt: e.target.value })}
        />
      </label>
    </div>
  )
}

function WorkspaceRow({
  workspace,
  workspaces,
  channelLabel,
  isDefault,
  isAdmin,
  editing,
  draft,
  loadError,
  saving,
  saveError,
  onStartEdit,
  onCancelEdit,
  onDraftChange,
  onSave,
}: {
  workspace: WorkspaceOption
  workspaces: Array<WorkspaceOption>
  channelLabel: (id: string) => string
  isDefault: boolean
  isAdmin: boolean
  editing: boolean
  draft: WorkspaceDraft | null
  loadError: string | null
  saving: boolean
  saveError: string | null
  onStartEdit: () => void
  onCancelEdit: () => void
  onDraftChange: (next: WorkspaceDraft) => void
  onSave: () => void
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
          <span className={`text-xs ${REFRESH_CLASS[status]}`}>
            {refreshLabel(status, workspace.refresh_kind)}
            {status !== "refreshing" && when ? ` ${when}` : ""}
          </span>
          {isAdmin && (
            <Button
              size="sm"
              variant="outline"
              aria-label={`${editing ? "Close" : "Edit"} ${workspace.name}`}
              aria-expanded={editing}
              onClick={editing ? onCancelEdit : onStartEdit}
            >
              {editing ? "Close" : "Edit"}
            </Button>
          )}
        </div>
      </div>
      <Chips values={workspace.repos} />
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
      {editing && draft && (
        <div className="-mx-4">
          <WorkspaceEditor
            draft={draft}
            onChange={onDraftChange}
            promptHint="Instructions appended to every run in this workspace"
            workspaceSlug={workspace.slug}
            workspaces={workspaces}
            channelLabel={channelLabel}
          />
          <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3.5">
            {saveError && (
              <p role="alert" className="text-xs text-destructive">
                {saveError}
              </p>
            )}
            <div className="ml-auto flex gap-2">
              <Button
                size="sm"
                variant="ghost"
                disabled={saving}
                onClick={onCancelEdit}
              >
                Cancel
              </Button>
              <Button size="sm" disabled={saving} onClick={onSave}>
                {saving ? "Saving…" : "Save"}
              </Button>
            </div>
          </div>
        </div>
      )}
      {editing && !draft && (
        <div className="-mx-4 border-t border-border px-4 py-3.5 text-xs">
          {loadError ? (
            <p role="alert" className="text-destructive">
              {loadError}
            </p>
          ) : (
            <p className="text-muted-foreground">Loading workspace…</p>
          )}
        </div>
      )}
    </div>
  )
}

export function WorkspacesSection({ isAdmin }: { isAdmin: boolean }) {
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

  const [editingSlug, setEditingSlug] = useState<string | null>(null)
  const [draft, setDraft] = useState<WorkspaceDraft | null>(null)
  const [editLoadError, setEditLoadError] = useState<string | null>(null)
  // Mirrors `editingSlug` for reads inside async callbacks below: a fetch or
  // save started for one workspace must not clobber another row's state if
  // the admin has since switched (or closed) the edit target before it
  // resolves.
  const editingSlugRef = useRef<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const [adding, setAdding] = useState(false)
  const [createDraft, setCreateDraft] = useState<WorkspaceDraft>(EMPTY_DRAFT)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const startEdit = (workspace: WorkspaceOption) => {
    editingSlugRef.current = workspace.slug
    setEditingSlug(workspace.slug)
    setDraft(null)
    setSaveError(null)
    setEditLoadError(null)
    void api.getWorkspace(workspace.slug).then(
      (record) => {
        if (editingSlugRef.current !== workspace.slug) return
        setDraft(draftFromWorkspace(record))
      },
      (e) => {
        if (editingSlugRef.current !== workspace.slug) return
        setEditLoadError(
          e instanceof Error ? e.message : "Could not load the workspace"
        )
      }
    )
  }

  const cancelEdit = () => {
    editingSlugRef.current = null
    setEditingSlug(null)
    setDraft(null)
    setSaveError(null)
    setEditLoadError(null)
  }

  const save = async (slug: string) => {
    if (!draft) return
    setSaving(true)
    setSaveError(null)
    try {
      const body: WorkspaceUpdate = {
        name: draft.name.trim(),
        repos: draft.repos,
        slack_channel_ids: draft.slackChannelIds,
        // The draft is only ever populated from the full record, so a blank
        // field here is the admin deliberately clearing the instructions.
        prompt: draft.prompt.trim(),
      }
      await api.updateWorkspace(slug, body)
      await qc.invalidateQueries({ queryKey: WORKSPACE_OPTIONS_KEY })
      // Only clear the shared edit state if it still belongs to this save —
      // the admin may have opened a different row while this one was saving.
      if (editingSlugRef.current === slug) cancelEdit()
    } catch (e) {
      setSaveError(
        e instanceof Error ? e.message : "Could not save the workspace"
      )
    }
    setSaving(false)
  }

  const create = async () => {
    setCreating(true)
    setCreateError(null)
    try {
      const body: WorkspaceCreate = {
        name: createDraft.name.trim(),
        repos: createDraft.repos,
        slack_channel_ids: createDraft.slackChannelIds,
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
          ? "Each workspace is rebuilt nightly from its setup script and, while in use, updated hourly by its update script. Edit its name, repositories, Slack channels, and instructions below. To change its scripts, start a new agent thread, open the + menu, enable admin mode, and ask Open SWE to make the change."
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
            workspaces={options.workspaces}
            channelLabel={channelLabel}
            isDefault={workspace.slug === options.default_slug}
            isAdmin={isAdmin}
            editing={editingSlug === workspace.slug}
            draft={editingSlug === workspace.slug ? draft : null}
            loadError={editingSlug === workspace.slug ? editLoadError : null}
            saving={saving}
            saveError={editingSlug === workspace.slug ? saveError : null}
            onStartEdit={() => startEdit(workspace)}
            onCancelEdit={cancelEdit}
            onDraftChange={setDraft}
            onSave={() => void save(workspace.slug)}
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
