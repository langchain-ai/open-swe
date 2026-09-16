import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useWorkspaceOptions,
  workspaceOptionKeys,
} from "@/features/agents/lib/queries"
import { api, type WorkspaceOption, type WorkspaceRecord } from "@/lib/api"
import { MCPConnectionsSection } from "./MCPConnectionsSection"
import { ReviewTeamSettings } from "./ReviewTeamSettings"
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
  FableSection,
  LLMGatewaySection,
  WorkspaceDefaultsSection,
} from "./WorkspaceTeamSettingsSections"

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
      description="Its name, the instructions appended to every run, and what it owns. A repository or Slack channel belongs to exactly one workspace."
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
export function WorkspaceSettings({
  slug,
  canEdit,
}: {
  slug: string
  canEdit: boolean
}) {
  const qc = useQueryClient()
  const record = useQuery({
    queryKey: workspaceRecordKey(slug),
    queryFn: () => api.getWorkspace(slug),
  })
  const options = useWorkspaceOptions(true)
  // Model options follow the workspace: the Fable flag that gates some of
  // them is one of its settings.
  const modelOptions = useQuery({
    queryKey: ["options", slug],
    queryFn: () => api.options(slug),
  })
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
      />
      <WorkspaceDefaultsSection
        workspace={slug}
        models={(modelOptions.data?.models ?? []).filter(
          (model) => model.can_be_default !== false
        )}
        repositories={record.data.repos}
      />
      <LLMGatewaySection workspace={slug} />
      <FableSection workspace={slug} />
      <ReviewTeamSettings workspace={slug} canEdit={canEdit} />
      <MCPConnectionsSection key={slug} scope="workspace" workspace={slug} />
    </>
  )
}
