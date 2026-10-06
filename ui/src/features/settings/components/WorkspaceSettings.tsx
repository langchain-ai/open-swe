import { useEffect, useState, type ReactNode } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { ConfirmableAction } from "@langchain/gtm-platform-design-system/patterns/confirmable-action"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import {
  SettingRow,
  SettingSection,
} from "@langchain/gtm-platform-design-system/patterns/setting-section"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { AlertTriangle, Loader2, Trash2 } from "@/components/glyphs"
import { WorkspaceRepositoriesSection } from "./WorkspaceRepositoriesSection"
import { WorkspaceApiKeysSection } from "./WorkspaceApiKeysSection"
import {
  useWorkspaceOptions,
  workspaceOptionKeys,
} from "@/features/agents/lib/queries"
import {
  api,
  DEFAULT_WORKSPACE_SLUG,
  type WorkspaceOption,
  type WorkspaceRecord,
} from "@/lib/api"
import { MCPConnectionsSection } from "./MCPConnectionsSection"
import { ExpeditedReviewSection } from "./ExpeditedReviewSection"
import { ReviewSettings } from "./ReviewSettings"
import {
  slackChannelLabel,
  useSlackChannelDirectory,
} from "@/lib/slack-channels"
import {
  draftFromWorkspace,
  WorkspaceEditor,
  type WorkspaceDraft,
} from "./WorkspaceEditor"
import { FormError, WorkspaceSandboxSection } from "./WorkspaceSandboxSection"
import { WorkspaceProxySection } from "./WorkspaceProxySection"
import {
  DefaultRepoSection,
  LLMGatewaySection,
  ModelDefaultsSection,
} from "./WorkspaceSettingsSections"
import type { SettingsScope } from "@/features/settings/lib/settingsScope"
import { invalidationTopic } from "@/lib/invalidations/topics"
import { useOptions, useRepos } from "@/lib/profile"

export const workspaceRecordKey = (slug: string) => ["workspace", slug] as const

function GeneralSection({
  record,
  workspaces,
  channelLabel,
  onSaved,
  rebuildStatus,
}: {
  record: WorkspaceRecord
  workspaces: Array<WorkspaceOption>
  channelLabel: (id: string) => string
  onSaved: (saved: WorkspaceRecord) => void
  rebuildStatus: ReactNode
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
        kitchen_channel_ids: draft.kitchenChannelIds,
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
    <PageSection
      contained
      inset="padded"
      title="General"
      description="Its name, the instructions appended to every run, its bound repositories, and its Slack channels. A repository is bound to one workspace, and a Slack channel belongs to one workspace."
    >
      <WorkspaceEditor
        draft={draft}
        onChange={setDraft}
        promptHint="Instructions appended to every run in this workspace"
        workspaceSlug={record.slug}
        workspaces={workspaces}
        channelLabel={channelLabel}
      />
      <Inline
        gap="sm"
        align="center"
        wrap
        className="border-t border-line pt-3"
      >
        {rebuildStatus}
        {error && <FormError message={error} />}
        <Inline gap="sm" className="ml-auto">
          {dirty && (
            <Button
              size="compact"
              variant="ghost"
              disabled={saving}
              onClick={() => setDraft(draftFromWorkspace(record))}
            >
              Cancel
            </Button>
          )}
          <Button
            size="compact"
            disabled={!dirty || saving || !draft.name.trim()}
            onClick={() => void save()}
          >
            {saving ? "Saving…" : "Save"}
          </Button>
        </Inline>
      </Inline>
    </PageSection>
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
  const [repositoryRebuild, setRepositoryRebuild] = useState<{
    slug: string
    finishedAt: WorkspaceRecord["refresh_finished_at"]
  } | null>(null)
  const [repositoryRebuildTimedOut, setRepositoryRebuildTimedOut] =
    useState(false)
  useEffect(() => {
    if (!repositoryRebuild) return
    const timeout = setTimeout(() => setRepositoryRebuildTimedOut(true), 60_000)
    return () => clearTimeout(timeout)
  }, [repositoryRebuild])
  const awaitingRepositoryRebuild = (workspace: WorkspaceRecord | undefined) =>
    repositoryRebuild?.slug === slug &&
    (!workspace ||
      workspace.refresh_finished_at === repositoryRebuild.finishedAt ||
      !["success", "failed"].includes(workspace.refresh_status ?? "never"))
  // The confirmation states a failure itself, so the toast stays quiet.
  const deleteWorkspace = useMutation({
    meta: { errorTitle: "Couldn't delete workspace", silent: true },
    mutationFn: api.deleteWorkspace,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: workspaceOptionKeys.all })
      onDeleted()
    },
  })
  const record = useQuery({
    queryKey: workspaceRecordKey(slug),
    queryFn: () => api.getWorkspace(slug),
    meta: { invalidatedBy: [invalidationTopic("workspaces")] },
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

  if (record.isLoading)
    return <Skeleton className="h-64 w-full rounded-panel" />
  if (record.isError || !record.data) {
    return (
      <StateNotice
        tone="RISK"
        icon={AlertTriangle}
        title="This workspace did not load"
        description={
          record.error instanceof Error
            ? record.error.message
            : "Could not load this workspace."
        }
      />
    )
  }

  const buildAction = record.data.snapshot_id ? "Rebuild" : "Build"
  const onSaved = (saved: WorkspaceRecord) => {
    const previousRepos = new Set(
      record.data.repos.map((repo) => repo.toLowerCase())
    )
    const savedRepos = new Set(saved.repos.map((repo) => repo.toLowerCase()))
    if (
      saved.setup_script &&
      (previousRepos.size !== savedRepos.size ||
        [...savedRepos].some((repo) => !previousRepos.has(repo)))
    ) {
      setRepositoryRebuildTimedOut(false)
      setRepositoryRebuild({
        slug,
        finishedAt: saved.refresh_finished_at,
      })
    }
    qc.setQueryData(workspaceRecordKey(slug), saved)
    void qc.invalidateQueries({ queryKey: workspaceOptionKeys.all })
  }
  const onRebuildStarted = async () => {
    // Show the run as underway at once, then let the poll confirm it, so a
    // second click cannot slip in before the record catches up.
    await qc.cancelQueries({ queryKey: workspaceRecordKey(slug) })
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
        rebuildStatus={
          (!repositoryRebuildTimedOut &&
            awaitingRepositoryRebuild(record.data)) ||
          record.data.refresh_status === "refreshing" ? (
            <Inline
              render={<p />}
              role="status"
              gap="sm"
              align="center"
              className="min-w-48 flex-1 text-meta text-ink-subtle"
            >
              <Icon
                icon={Loader2}
                size="sm"
                className="animate-spin motion-reduce:animate-none"
              />
              <Box render={<span />}>
                {record.data.refresh_status === "refreshing"
                  ? `${buildAction}ing sandbox image…`
                  : `Repositories saved. Sandbox image ${buildAction.toLowerCase()} queued…`}{" "}
                Existing runs keep their current image.
              </Box>
            </Inline>
          ) : awaitingRepositoryRebuild(record.data) ? (
            <Box
              render={<p />}
              role="alert"
              ink="risk"
              className="min-w-48 flex-1 text-label"
            >
              Repositories saved, but the image {buildAction.toLowerCase()}{" "}
              could not be confirmed. Check the sandbox image status or retry{" "}
              {buildAction} image.
            </Box>
          ) : repositoryRebuild?.slug === slug ? (
            <Box
              render={<p />}
              role={
                record.data.refresh_status === "failed" ? "alert" : "status"
              }
              ink={
                record.data.refresh_status === "failed" ? "risk" : "ink-subtle"
              }
              className="min-w-48 flex-1 text-meta"
            >
              {record.data.refresh_status === "failed"
                ? `Image ${buildAction.toLowerCase()} failed. ${record.data.refresh_error ?? (record.data.snapshot_id ? "The previous image is still in use." : "No image is available yet.")}`
                : "Sandbox image built with the saved repositories."}
            </Box>
          ) : null
        }
      />
      <WorkspaceSandboxSection
        key={`sandbox:${record.data.setup_script ?? ""}:${record.data.update_script ?? ""}`}
        record={record.data}
        onSaved={onSaved}
        onRebuildStarted={onRebuildStarted}
      />
      <WorkspaceProxySection
        key={`proxy:${slug}:${JSON.stringify(record.data.create_params)}`}
        record={record.data}
        canEdit={canEdit}
        onSaved={onSaved}
      />
      <ModelDefaultsSection
        scope={scope}
        models={(modelOptions.data?.models ?? []).filter(
          (model) => model.can_be_default !== false
        )}
      />
      <DefaultRepoSection
        scope={scope}
        repositories={repositories.data?.repositories ?? []}
      />
      <WorkspaceRepositoriesSection slug={slug} canEdit={canEdit} />
      {canEdit && (
        <WorkspaceApiKeysSection key={`api-keys:${slug}`} slug={slug} />
      )}
      <LLMGatewaySection scope={scope} />
      <ReviewSettings scope={scope} canEdit={canEdit} />
      <ExpeditedReviewSection scope={scope} />
      <MCPConnectionsSection key={slug} scope="workspace" workspace={slug} />
      {canEdit && slug !== DEFAULT_WORKSPACE_SLUG && (
        <SettingSection
          contained
          title="Delete workspace"
          description="Permanently delete this workspace, its settings, and sandbox snapshot. This cannot be undone."
        >
          <SettingRow
            label={record.data.name}
            description="Releases its repository and Slack channel bindings."
            density="compact"
            control={() => (
              <ConfirmableAction
                trigger={
                  <Button
                    size="compact"
                    variant="outline"
                    className="border-risk text-risk hover:bg-risk-bg"
                    aria-label={`Delete ${record.data.name}`}
                  >
                    <Icon icon={Trash2} size="sm" />
                    Delete
                  </Button>
                }
                title={`Delete ${record.data.name}?`}
                description="This deletes the workspace, its settings and sandbox snapshot, and releases its repository and Slack channel bindings. This cannot be undone."
                confirmLabel="Delete workspace"
                onConfirm={() => deleteWorkspace.mutateAsync(slug)}
              />
            )}
          />
        </SettingSection>
      )}
    </>
  )
}
