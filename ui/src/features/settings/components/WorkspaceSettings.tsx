import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"

import { SettingsSection } from "@/components/AppShell"
import { WorkspaceRepositoriesSection } from "./WorkspaceRepositoriesSection"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useWorkspaceOptions,
  workspaceOptionKeys,
} from "@/features/agents/lib/queries"
import { api, type WorkspaceOption, type WorkspaceRecord } from "@/lib/api"
import { MCPConnectionsSection } from "./MCPConnectionsSection"
import { ExpeditedReviewSection } from "./ExpeditedReviewSection"
import { ReviewSettings } from "./ReviewSettings"
import {
  slackChannelLabel,
  useSlackChannelDirectory,
} from "./WorkspaceBindingPickers"
import {
  draftFromWorkspace,
  WorkspaceEditor,
  type WorkspaceDraft,
} from "./WorkspaceEditor"
import { WorkspaceSandboxSection } from "./WorkspaceSandboxSection"
import {
  DefaultRepoSection,
  FableSection,
  LLMGatewaySection,
  ModelDefaultsSection,
} from "./WorkspaceSettingsSections"
import type { SettingsScope } from "@/features/settings/lib/settingsScope"
import { useOptions, useRepos } from "@/lib/profile"

export const workspaceRecordKey = (slug: string) => ["workspace", slug] as const

function GeneralSection({
  record,
  workspaces,
  channelLabel,
  onSaved,
}: {
  record: WorkspaceRecord
  workspaces: Array<WorkspaceOption>
  channelLabel: (id: string) => string
  onSaved: (saved: WorkspaceRecord) => void
}) {
  const [draft, setDraft] = useState<WorkspaceDraft>(() =>
    draftFromWorkspace(record)
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const dirty =
    JSON.stringify(draft) !== JSON.stringify(draftFromWorkspace(record))

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const saved = await api.updateWorkspace(record.slug, {
        name: draft.name.trim(),
        repos: draft.repos,
        all_repositories: draft.allRepositories,
        slack_channel_ids: draft.slackChannelIds,
        prompt: draft.prompt,
      })
      onSaved(saved)
      setDraft(draftFromWorkspace(saved))
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the workspace")
    } finally {
      setSaving(false)
    }
  }

  return (
    <SettingsSection
      title="General"
      description="Its name, instructions, and repository permissions. Repositories may be shared; Slack channels belong to one workspace. Permission changes do not rebuild the sandbox image."
    >
      <WorkspaceEditor
        draft={draft}
        onChange={setDraft}
        promptHint="Instructions appended to every run in this workspace"
        workspaceSlug={record.slug}
        workspaces={workspaces}
        channelLabel={channelLabel}
      />
      <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3.5">
        {error && (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        )}
        <div className="ml-auto flex gap-2">
          <Button
            size="sm"
            variant="ghost"
            disabled={!dirty || saving}
            onClick={() => setDraft(draftFromWorkspace(record))}
          >
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={!dirty || saving || !draft.name.trim()}
            onClick={() => void save()}
          >
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>
    </SettingsSection>
  )
}

/** Everything configured per workspace, on one page. */
export function WorkspaceSettingsPanel({
  slug,
  canEdit,
  onDeleted,
}: {
  slug: string
  canEdit: boolean
  onDeleted: () => void
}) {
  const qc = useQueryClient()
  const [deleting, setDeleting] = useState(false)
  const deleteWorkspace = useMutation({
    mutationFn: api.deleteWorkspace,
    onSuccess: async () => {
      setDeleting(false)
      await qc.invalidateQueries({ queryKey: workspaceOptionKeys.all })
      onDeleted()
    },
  })
  const record = useQuery({
    queryKey: workspaceRecordKey(slug),
    queryFn: () => api.getWorkspace(slug),
    refetchInterval: (query) =>
      query.state.data?.refresh_status === "refreshing" ? 5000 : false,
  })
  const options = useWorkspaceOptions(true)
  const repositories = useRepos()
  // Model options follow the workspace: the Fable flag that gates some of
  // them is one of its settings.
  const modelOptions = useOptions(slug)
  const scope: SettingsScope = { kind: "workspace", slug }
  const channelDirectory = useSlackChannelDirectory(canEdit)
  const channelLabel = (id: string) =>
    slackChannelLabel(channelDirectory.data, id)

  if (record.isLoading) return <Skeleton className="h-64 w-full" />
  if (record.isError || !record.data) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {record.error instanceof Error
          ? record.error.message
          : "Could not load this workspace."}
      </p>
    )
  }

  const onSaved = (saved: WorkspaceRecord) => {
    qc.setQueryData(workspaceRecordKey(slug), saved)
    void qc.invalidateQueries({ queryKey: workspaceOptionKeys.all })
  }
  const onRebuildStarted = () => {
    // Show the run as underway at once, then let the poll confirm it, so a
    // second click cannot slip in before the record catches up.
    qc.setQueryData(
      workspaceRecordKey(slug),
      (current: WorkspaceRecord | undefined) =>
        current ? { ...current, refresh_status: "refreshing" } : current
    )
    void qc.invalidateQueries({ queryKey: workspaceRecordKey(slug) })
    void qc.invalidateQueries({ queryKey: workspaceOptionKeys.all })
  }

  return (
    <>
      <GeneralSection
        // Remount when the record changes underneath so the draft follows it.
        key={JSON.stringify(draftFromWorkspace(record.data))}
        record={record.data}
        workspaces={options.data?.workspaces ?? []}
        channelLabel={channelLabel}
        onSaved={onSaved}
      />
      <WorkspaceSandboxSection
        key={`sandbox:${record.data.setup_script ?? ""}:${record.data.update_script ?? ""}`}
        record={record.data}
        onSaved={onSaved}
        onRebuildStarted={onRebuildStarted}
      />
      <ModelDefaultsSection
        scope={scope}
        models={(modelOptions.data?.models ?? []).filter(
          (model) => model.can_be_default !== false
        )}
      />
      <DefaultRepoSection
        scope={scope}
        repositories={
          record.data.all_repositories
            ? (repositories.data?.repositories ?? []).map(
                (repo) => repo.full_name
              )
            : record.data.repos
        }
      />
      <WorkspaceRepositoriesSection slug={slug} canEdit={canEdit} />
      <LLMGatewaySection scope={scope} />
      <FableSection scope={scope} />
      <ReviewSettings scope={scope} canEdit={canEdit} />
      <ExpeditedReviewSection scope={scope} />
      <MCPConnectionsSection key={slug} scope="workspace" workspace={slug} />
      {canEdit && (
        <SettingsSection
          title="Delete workspace"
          description="Permanently delete this workspace, its settings, and sandbox snapshot. This cannot be undone."
        >
          <div className="px-4 py-3.5">
            <Button
              size="sm"
              variant="destructive"
              aria-label={`Delete ${record.data.name}`}
              onClick={() => {
                deleteWorkspace.reset()
                setDeleting(true)
              }}
            >
              Delete
            </Button>
          </div>
        </SettingsSection>
      )}
      {canEdit && deleting && (
        <AlertDialog
          open
          onOpenChange={(open) => {
            if (!open && !deleteWorkspace.isPending) setDeleting(false)
          }}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete {record.data.name}?</AlertDialogTitle>
              <AlertDialogDescription>
                This deletes the workspace, its settings and sandbox snapshot,
                and releases its repository and Slack channel bindings. This
                cannot be undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            {deleteWorkspace.error && (
              <p role="alert" className="text-xs text-destructive">
                {deleteWorkspace.error.message}
              </p>
            )}
            <AlertDialogFooter>
              <AlertDialogCancel disabled={deleteWorkspace.isPending}>
                Cancel
              </AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                disabled={deleteWorkspace.isPending}
                onClick={() => deleteWorkspace.mutate(slug)}
              >
                {deleteWorkspace.isPending ? "Deleting…" : "Delete workspace"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </>
  )
}
