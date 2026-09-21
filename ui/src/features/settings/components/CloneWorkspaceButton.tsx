import { CopyIcon } from "@phosphor-icons/react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { api, type WorkspaceOption, type WorkspaceRecord } from "@/lib/api"
import { WorkspaceEditor, type WorkspaceDraft } from "./WorkspaceEditor"

export function CloneWorkspaceButton({
  workspace,
  workspaces,
  channelLabel,
  onCloned,
}: {
  workspace: WorkspaceRecord
  workspaces: Array<WorkspaceOption>
  channelLabel: (id: string) => string
  onCloned: (slug: string) => void
}) {
  const initialDraft = (): WorkspaceDraft => ({
    name: `${workspace.name} copy`,
    repos: [],
    slackChannelIds: [],
    prompt: workspace.prompt,
  })
  const [draft, setDraft] = useState(initialDraft)
  const [open, setOpen] = useState(false)
  const [cloning, setCloning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const clone = async () => {
    setCloning(true)
    setError(null)
    try {
      const cloned = await api.createWorkspace({
        name: draft.name.trim(),
        prompt: draft.prompt,
        repos: draft.repos,
        slack_channel_ids: draft.slackChannelIds,
        setup_script: workspace.setup_script,
        update_script: workspace.update_script,
        base_snapshot_id: workspace.base_snapshot_id,
        mem_bytes: workspace.mem_bytes,
        vcpus: workspace.vcpus,
        fs_capacity_bytes: workspace.fs_capacity_bytes,
        create_params: workspace.create_params,
      })
      onCloned(cloned.slug)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not clone the workspace")
    } finally {
      setCloning(false)
    }
  }

  if (!open) {
    return (
      <Button
        size="sm"
        variant="outline"
        onClick={() => {
          setDraft(initialDraft())
          setError(null)
          setOpen(true)
        }}
      >
        <CopyIcon />
        Clone workspace
      </Button>
    )
  }

  return (
    <section className="w-full space-y-3">
      <div>
        <h2 className="text-sm font-medium text-foreground">Clone workspace</h2>
        <p className="mt-1 max-w-2xl text-xs text-muted-foreground">
          Instructions and sandbox configuration are copied. Choose new
          repositories and Slack channels because each can belong to only one
          workspace. The published image stays with the original.
        </p>
      </div>
      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <WorkspaceEditor
          draft={draft}
          onChange={setDraft}
          promptHint="Instructions appended to every run in this workspace"
          workspaceSlug={null}
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
              disabled={cloning}
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={
                cloning || !draft.name.trim() || draft.repos.length === 0
              }
              onClick={() => void clone()}
            >
              {cloning ? "Cloning…" : "Clone workspace"}
            </Button>
          </div>
        </div>
      </div>
    </section>
  )
}
