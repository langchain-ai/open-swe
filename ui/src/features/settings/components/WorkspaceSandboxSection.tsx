import { useState } from "react"
import { useMutation } from "@tanstack/react-query"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover"
import { api, type WorkspaceRecord } from "@/lib/api"

import { WorkspaceScriptEditor } from "./WorkspaceScriptEditor"

const SNAPSHOT_LABEL: Record<
  NonNullable<WorkspaceRecord["snapshot_status"]>,
  string
> = {
  none: "No image yet",
  capturing: "Capturing…",
  ready: "Image ready",
  failed: "Capture failed",
}

function WorkspaceReposPopover({ repos }: { repos: string[] }) {
  return (
    <Popover>
      <PopoverTrigger className="cursor-pointer rounded-sm font-mono underline decoration-dotted underline-offset-4 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring">
        OPENSWE_WORKSPACE_REPOS
      </PopoverTrigger>
      <PopoverPopup align="start" className="w-96 max-w-[calc(100vw-2rem)]">
        <PopoverTitle>Expanded value</PopoverTitle>
        <pre className="mt-2 max-h-60 overflow-auto rounded-md bg-muted p-3 font-mono text-xs break-all whitespace-pre-wrap">
          <code>{`OPENSWE_WORKSPACE_REPOS="${repos.join(" ")}"`}</code>
        </pre>
      </PopoverPopup>
    </Popover>
  )
}

function resourceValue(value: number | null | undefined, divisor = 1) {
  return value == null ? "" : String(value / divisor)
}

function resourceBytes(value: string, multiplier = 1): number | null {
  if (!value.trim()) return null
  const number = Number(value) * multiplier
  if (!Number.isSafeInteger(number) || number <= 0)
    throw new Error(
      "Sandbox sizes must be positive and resolve to whole bytes or vCPUs."
    )
  return number
}

/** Configure the workspace's new sandboxes and image scripts. */
export function WorkspaceSandboxSection({
  record,
  onSaved,
  onRebuildStarted,
}: {
  record: WorkspaceRecord
  onSaved: (saved: WorkspaceRecord) => void
  onRebuildStarted: () => void
}) {
  const buildAction = record.snapshot_id ? "Rebuild" : "Build"
  const [setupScript, setSetupScript] = useState(record.setup_script ?? "")
  const [updateScript, setUpdateScript] = useState(record.update_script ?? "")
  const [vcpus, setVcpus] = useState(resourceValue(record.vcpus))
  const [memory, setMemory] = useState(
    resourceValue(record.mem_bytes, 1024 ** 3)
  )
  const [disk, setDisk] = useState(
    resourceValue(record.fs_capacity_bytes, 1024 ** 3)
  )
  const configurationDirty =
    vcpus !== resourceValue(record.vcpus) ||
    memory !== resourceValue(record.mem_bytes, 1024 ** 3) ||
    disk !== resourceValue(record.fs_capacity_bytes, 1024 ** 3)
  const dirty =
    setupScript !== (record.setup_script ?? "") ||
    updateScript !== (record.update_script ?? "")

  const configuration = useMutation({
    meta: { silent: true },
    mutationFn: async () => {
      return api.updateWorkspace(record.slug, {
        vcpus: resourceBytes(vcpus),
        mem_bytes: resourceBytes(memory, 1024 ** 3),
        fs_capacity_bytes: resourceBytes(disk, 1024 ** 3),
      })
    },
    onSuccess: onSaved,
  })
  const save = useMutation({
    meta: { silent: true },
    mutationFn: () =>
      api.updateWorkspace(record.slug, {
        setup_script: setupScript,
        update_script: updateScript,
      }),
    onSuccess: onSaved,
  })
  const rebuild = useMutation({
    meta: {
      errorTitle: `Couldn't start the image ${buildAction.toLowerCase()}`,
    },
    mutationFn: () => api.refreshWorkspace(record.slug),
    onSuccess: onRebuildStarted,
  })

  const status = record.snapshot_status ?? "none"
  const refreshing = record.refresh_status === "refreshing"

  return (
    <SettingsSection
      title="Sandbox image"
      description="Every run in this workspace boots from this image. The setup script builds it nightly from the base snapshot; the update script refreshes it while it is in use."
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
      <div className="space-y-3 border-b border-border px-4 py-3.5">
        <p className="text-xs text-muted-foreground">
          Applies to new sandboxes and image builders, not existing threads.
          Leave sizes blank to inherit deployment defaults. If only CPU or
          memory is set, the sandbox service chooses the other.
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {[
            { label: "vCPUs", value: vcpus, set: setVcpus, step: "1" },
            {
              label: "Memory (GiB)",
              value: memory,
              set: setMemory,
              step: "any",
            },
            { label: "Disk (GiB)", value: disk, set: setDisk, step: "any" },
          ].map(({ label, value, set, step }) => (
            <label key={label} className="text-sm">
              {label}
              <Input
                aria-label={label}
                type="number"
                min="0"
                step={step}
                placeholder="Deployment default"
                value={value}
                onChange={(event) => set(event.target.value)}
              />
            </label>
          ))}
        </div>
        {configuration.error && (
          <p role="alert" className="text-xs text-destructive">
            {configuration.error.message}
          </p>
        )}
        <div className="flex justify-end gap-2">
          {configurationDirty && (
            <Button
              size="sm"
              variant="ghost"
              disabled={configuration.isPending}
              onClick={() => {
                setVcpus(resourceValue(record.vcpus))
                setMemory(resourceValue(record.mem_bytes, 1024 ** 3))
                setDisk(resourceValue(record.fs_capacity_bytes, 1024 ** 3))
              }}
            >
              Cancel
            </Button>
          )}
          <Button
            size="sm"
            disabled={!configurationDirty || configuration.isPending}
            onClick={() => configuration.mutate()}
          >
            {configuration.isPending ? "Saving…" : "Save sandbox configuration"}
          </Button>
        </div>
      </div>
      <div className="space-y-3 px-4 py-3.5">
        <div className="text-sm">
          <div>Setup script</div>
          <span className="mt-0.5 block text-xs text-muted-foreground">
            Runs on the base snapshot to build the image. Bound repositories are
            available in <WorkspaceReposPopover repos={record.repos} /> to
            preload; runs clone any other repository on demand.
          </span>
          <WorkspaceScriptEditor
            label="Setup script"
            value={setupScript}
            onChange={setSetupScript}
          />
        </div>
        <div className="text-sm">
          <div>Update script</div>
          <span className="mt-0.5 block text-xs text-muted-foreground">
            Runs on the current image to bring it up to date, with the same{" "}
            <WorkspaceReposPopover repos={record.repos} /> value.
          </span>
          <WorkspaceScriptEditor
            label="Update script"
            value={updateScript}
            onChange={setUpdateScript}
          />
        </div>
        {save.error && (
          <p role="alert" className="text-xs text-destructive">
            {save.error.message}
          </p>
        )}
        {rebuild.isSuccess && (
          <p className="text-xs text-muted-foreground">
            {buildAction} started; the image state above follows its progress.
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
            {refreshing ? `${buildAction}ing…` : `${buildAction} image`}
          </Button>
          <div className="flex gap-2">
            {dirty && (
              <Button
                size="sm"
                variant="ghost"
                disabled={save.isPending}
                onClick={() => {
                  setSetupScript(record.setup_script ?? "")
                  setUpdateScript(record.update_script ?? "")
                }}
              >
                Cancel
              </Button>
            )}
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
