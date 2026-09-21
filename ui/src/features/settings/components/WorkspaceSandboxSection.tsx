import { useState } from "react"
import { useMutation } from "@tanstack/react-query"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { api, type WorkspaceRecord } from "@/lib/api"

const SNAPSHOT_LABEL: Record<
  NonNullable<WorkspaceRecord["snapshot_status"]>,
  string
> = {
  none: "No image yet",
  capturing: "Capturing…",
  ready: "Image ready",
  failed: "Capture failed",
}

function gib(bytes: number | null | undefined): string | null {
  if (bytes === null || bytes === undefined) return null
  return `${Math.round((bytes / 1024 ** 3) * 10) / 10} GiB`
}

/**
 * The scripts that build and refresh a workspace's sandbox image, with the
 * image's current state. Sizing and the base snapshot are set when the image
 * is published from an admin thread, so they are shown rather than edited.
 */
export function WorkspaceSandboxSection({
  record,
  onSaved,
  onRebuildStarted,
}: {
  record: WorkspaceRecord
  onSaved: (saved: WorkspaceRecord) => void
  onRebuildStarted: () => void
}) {
  const [setupScript, setSetupScript] = useState(record.setup_script ?? "")
  const [updateScript, setUpdateScript] = useState(record.update_script ?? "")
  const dirty =
    setupScript !== (record.setup_script ?? "") ||
    updateScript !== (record.update_script ?? "")

  const save = useMutation({
    mutationFn: () =>
      api.updateWorkspace(record.slug, {
        setup_script: setupScript,
        update_script: updateScript,
      }),
    onSuccess: onSaved,
  })
  const rebuild = useMutation({
    mutationFn: () => api.refreshWorkspace(record.slug),
    onSuccess: onRebuildStarted,
  })

  const status = record.snapshot_status ?? "none"
  const refreshing = record.refresh_status === "refreshing"
  const workspaceRepos = record.repos.join(" ")
  const sizing = [
    record.vcpus === null || record.vcpus === undefined
      ? null
      : `${record.vcpus} vCPU`,
    gib(record.mem_bytes),
    gib(record.fs_capacity_bytes) && `${gib(record.fs_capacity_bytes)} disk`,
  ].filter((part): part is string => !!part)

  return (
    <SettingsSection
      title="Sandbox image"
      description="Every run in this workspace boots from this image. The setup script rebuilds it nightly from the base snapshot; the update script refreshes it while it is in use."
    >
      <SettingsRow
        label="Image"
        description={record.status_message ?? record.snapshot_name ?? undefined}
        control={
          <span className="text-xs text-muted-foreground">
            {SNAPSHOT_LABEL[status]}
          </span>
        }
      />
      <SettingsRow
        label="Base snapshot"
        description="Set when the image is published from an admin thread."
        control={
          <span className="font-mono text-xs text-muted-foreground">
            {record.base_snapshot_id ?? "instance default"}
          </span>
        }
      />
      {sizing.length > 0 && (
        <SettingsRow
          label="Sandbox size"
          control={
            <span className="text-xs text-muted-foreground">
              {sizing.join(" · ")}
            </span>
          }
        />
      )}
      <div className="space-y-3 px-4 py-3.5">
        <label className="block text-sm">
          Setup script
          <span className="mt-0.5 block text-xs text-muted-foreground">
            Runs on the base snapshot to build the image. Selected repositories
            are available in <code>OPENSWE_WORKSPACE_REPOS</code>
            {workspaceRepos && (
              <>
                {": "}
                <code>{workspaceRepos}</code>
              </>
            )}
            .
          </span>
          <Textarea
            aria-label="Setup script"
            className="mt-1 font-mono text-xs"
            placeholder="Install dependencies and build the image."
            value={setupScript}
            onChange={(e) => setSetupScript(e.target.value)}
          />
        </label>
        <label className="block text-sm">
          Update script
          <span className="mt-0.5 block text-xs text-muted-foreground">
            Runs on the current image to bring it up to date, with the same
            <code className="ml-1">OPENSWE_WORKSPACE_REPOS</code> value.
          </span>
          <Textarea
            aria-label="Update script"
            className="mt-1 font-mono text-xs"
            placeholder="Pull repositories and reinstall dependencies."
            value={updateScript}
            onChange={(e) => setUpdateScript(e.target.value)}
          />
        </label>
        {(save.error || rebuild.error) && (
          <p role="alert" className="text-xs text-destructive">
            {(save.error ?? rebuild.error)?.message}
          </p>
        )}
        {rebuild.isSuccess && (
          <p className="text-xs text-muted-foreground">
            Rebuild started; the image state above follows its progress.
          </p>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={
              rebuild.isPending || refreshing || !(record.setup_script ?? "")
            }
            onClick={() => rebuild.mutate()}
          >
            {refreshing ? "Rebuilding…" : "Rebuild image"}
          </Button>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="ghost"
              disabled={!dirty || save.isPending}
              onClick={() => {
                setSetupScript(record.setup_script ?? "")
                setUpdateScript(record.update_script ?? "")
              }}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate()}
            >
              {save.isPending ? "Saving…" : "Save scripts"}
            </Button>
          </div>
        </div>
      </div>
    </SettingsSection>
  )
}
