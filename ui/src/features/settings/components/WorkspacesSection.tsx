import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { SettingsSection } from "@/components/AppShell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import {
  api,
  type WorkspaceCreate,
  type WorkspaceOption,
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

/** A textarea's raw lines, trimmed and with blanks dropped. */
function parseLines(value: string): Array<string> {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
}

interface WorkspaceDraft {
  name: string
  repos: string
  slackChannelIds: string
  prompt: string
}

function draftFromWorkspace(workspace: WorkspaceOption): WorkspaceDraft {
  return {
    name: workspace.name,
    repos: workspace.repos.join("\n"),
    slackChannelIds: workspace.slack_channel_ids.join("\n"),
    prompt: "",
  }
}

const EMPTY_DRAFT: WorkspaceDraft = {
  name: "",
  repos: "",
  slackChannelIds: "",
  prompt: "",
}

function WorkspaceEditor({
  draft,
  onChange,
  promptHint,
}: {
  draft: WorkspaceDraft
  onChange: (next: WorkspaceDraft) => void
  promptHint: string
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
      <label className="block text-sm">
        Repositories
        <Textarea
          aria-label="Repositories"
          placeholder={"owner/repo\none per line"}
          value={draft.repos}
          onChange={(e) => onChange({ ...draft, repos: e.target.value })}
        />
      </label>
      <label className="block text-sm">
        Slack channel IDs
        <Textarea
          aria-label="Slack channel IDs"
          placeholder={"C0123456789\none per line"}
          value={draft.slackChannelIds}
          onChange={(e) =>
            onChange({ ...draft, slackChannelIds: e.target.value })
          }
        />
      </label>
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
  isDefault,
  isAdmin,
  editing,
  draft,
  saving,
  saveError,
  onStartEdit,
  onCancelEdit,
  onDraftChange,
  onSave,
}: {
  workspace: WorkspaceOption
  isDefault: boolean
  isAdmin: boolean
  editing: boolean
  draft: WorkspaceDraft | null
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
      <Chips values={workspace.slack_channel_ids} />
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
            promptHint="Leave blank to keep the current instructions"
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

  const [editingSlug, setEditingSlug] = useState<string | null>(null)
  const [draft, setDraft] = useState<WorkspaceDraft | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const [adding, setAdding] = useState(false)
  const [createDraft, setCreateDraft] = useState<WorkspaceDraft>(EMPTY_DRAFT)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const startEdit = (workspace: WorkspaceOption) => {
    setEditingSlug(workspace.slug)
    setDraft(draftFromWorkspace(workspace))
    setSaveError(null)
  }

  const cancelEdit = () => {
    setEditingSlug(null)
    setDraft(null)
    setSaveError(null)
  }

  const save = async (slug: string) => {
    if (!draft) return
    setSaving(true)
    setSaveError(null)
    try {
      const body: WorkspaceUpdate = {
        name: draft.name.trim(),
        repos: parseLines(draft.repos),
        slack_channel_ids: parseLines(draft.slackChannelIds),
      }
      const prompt = draft.prompt.trim()
      if (prompt) body.prompt = prompt
      await api.updateWorkspace(slug, body)
      await qc.invalidateQueries({ queryKey: WORKSPACE_OPTIONS_KEY })
      cancelEdit()
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
        repos: parseLines(createDraft.repos),
        slack_channel_ids: parseLines(createDraft.slackChannelIds),
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
            isDefault={workspace.slug === options.default_slug}
            isAdmin={isAdmin}
            editing={editingSlug === workspace.slug}
            draft={editingSlug === workspace.slug ? draft : null}
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
