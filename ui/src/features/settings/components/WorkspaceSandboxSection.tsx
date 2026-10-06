import { useState } from "react"
import { useMutation } from "@tanstack/react-query"

import {
  FormField,
  FormSection,
} from "@langchain/gtm-platform-design-system/patterns/form-field"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { SettingRow } from "@langchain/gtm-platform-design-system/patterns/setting-section"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Checkbox } from "@langchain/gtm-platform-design-system/ui/checkbox"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { AlertTriangle } from "@/components/glyphs"
import { api, type WorkspaceRecord } from "@/lib/api"

import { ScriptField, WorkspaceScriptEditor } from "./WorkspaceScriptEditor"

const SNAPSHOT_LABEL: Record<
  NonNullable<WorkspaceRecord["snapshot_status"]>,
  string
> = {
  none: "No image yet",
  capturing: "Capturing…",
  ready: "Image ready",
  failed: "Capture failed",
}

const SNAPSHOT_TONE: Record<
  NonNullable<WorkspaceRecord["snapshot_status"]>,
  "neutral" | "info" | "positive" | "risk"
> = {
  none: "neutral",
  capturing: "info",
  ready: "positive",
  failed: "risk",
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
  const [inheritDefault, setInheritDefault] = useState(
    record.inherit_default_sandbox ?? false
  )
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
    inheritDefault !== (record.inherit_default_sandbox ?? false) ||
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
        ...(inheritDefault !== (record.inherit_default_sandbox ?? false)
          ? { inherit_default_sandbox: inheritDefault }
          : {}),
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
  const sizes = [
    { label: "vCPUs", value: vcpus, set: setVcpus, step: "1" },
    { label: "Memory (GiB)", value: memory, set: setMemory, step: "any" },
    { label: "Disk (GiB)", value: disk, set: setDisk, step: "any" },
  ]

  return (
    <PageSection
      contained
      title="Sandbox image"
      description="Every run in this workspace boots from this image. The setup script builds it nightly from the base snapshot; the update script refreshes it while it is in use."
    >
      <Stack gap="none">
        <Stack gap="none" className="@container">
          <SettingRow
            label="Image"
            badge={
              record.inherit_default_sandbox ? (
                <Badge tier="quiet" tone="neutral">
                  Inherited from default
                </Badge>
              ) : (
                <Badge tier="quiet" tone={SNAPSHOT_TONE[status]}>
                  {SNAPSHOT_LABEL[status]}
                </Badge>
              )
            }
            description={
              rebuild.isSuccess
                ? `${buildAction} started; the image state follows its progress.`
                : (record.status_message ?? record.snapshot_name ?? undefined)
            }
            control={() => (
              <Button
                size="compact"
                variant="outline"
                disabled={
                  rebuild.isPending ||
                  refreshing ||
                  record.inherit_default_sandbox ||
                  !(record.setup_script ?? "")
                }
                onClick={() => rebuild.mutate()}
              >
                {refreshing ? `${buildAction}ing…` : `${buildAction} image`}
              </Button>
            )}
          />
          <SettingRow
            label="Base snapshot"
            description="Set when the image is published from an admin thread."
            control={() => (
              <Box
                render={<span />}
                className="truncate font-mono text-meta text-ink-subtle"
              >
                {record.base_snapshot_id ?? "instance default"}
              </Box>
            )}
          />
        </Stack>

        <Stack gap="lg" className="border-t border-line px-5 py-4">
          <FormSection
            title="Sandbox size"
            description="Applies to new sandboxes and image builders, not existing threads. Leave sizes blank to inherit deployment defaults. If only CPU or memory is set, the sandbox service chooses the other."
          >
            {record.slug !== "default" && (
              <Inline render={<label />} gap="sm" align="start">
                <Checkbox
                  aria-label="Inherit sandbox from default"
                  className="mt-0.5"
                  checked={inheritDefault}
                  onCheckedChange={setInheritDefault}
                />
                <Stack gap="xs">
                  <Box
                    render={<span />}
                    className="text-label font-medium text-ink"
                  >
                    Inherit sandbox from default
                  </Box>
                  <Box render={<span />} className="text-meta text-ink-subtle">
                    Use the default workspace’s latest image, sizing, creation
                    settings, and update script. Workspace instructions and
                    integrations remain independent. Ensure you’re comfortable
                    sharing all contents of the inherited sandbox image with
                    members of this workspace.
                  </Box>
                </Stack>
              </Inline>
            )}
            <Box className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {sizes.map(({ label, value, set, step }) => (
                <FormField
                  key={label}
                  label={label}
                  control={
                    <Input
                      type="number"
                      min="0"
                      step={step}
                      placeholder="Deployment default"
                      value={value}
                      onChange={(event) => set(event.target.value)}
                    />
                  }
                />
              ))}
            </Box>
          </FormSection>
          {configuration.error && (
            <FormError message={configuration.error.message} />
          )}
          <Inline gap="sm" justify="end">
            {configurationDirty && (
              <Button
                size="compact"
                variant="ghost"
                disabled={configuration.isPending}
                onClick={() => {
                  setInheritDefault(record.inherit_default_sandbox ?? false)
                  setVcpus(resourceValue(record.vcpus))
                  setMemory(resourceValue(record.mem_bytes, 1024 ** 3))
                  setDisk(resourceValue(record.fs_capacity_bytes, 1024 ** 3))
                }}
              >
                Cancel
              </Button>
            )}
            <Button
              size="compact"
              disabled={!configurationDirty || configuration.isPending}
              onClick={() => configuration.mutate()}
            >
              {configuration.isPending
                ? "Saving…"
                : "Save sandbox configuration"}
            </Button>
          </Inline>
        </Stack>

        <Stack gap="lg" className="border-t border-line px-5 py-4">
          <FormSection title="Image scripts">
            <ScriptField
              label="Setup script"
              help="Runs on the base snapshot to build the image."
            >
              <WorkspaceScriptEditor
                label="Setup script"
                repos={record.repos}
                value={setupScript}
                onChange={setSetupScript}
              />
            </ScriptField>
            <ScriptField
              label="Update script"
              help="Runs on the current image to bring it up to date."
            >
              <WorkspaceScriptEditor
                label="Update script"
                repos={record.repos}
                value={updateScript}
                onChange={setUpdateScript}
              />
            </ScriptField>
          </FormSection>
          {save.error && <FormError message={save.error.message} />}
          <Inline gap="sm" justify="end">
            {dirty && (
              <Button
                size="compact"
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
              size="compact"
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate()}
            >
              {save.isPending ? "Saving…" : "Save scripts"}
            </Button>
          </Inline>
        </Stack>
      </Stack>
    </PageSection>
  )
}

/** A save the server refused, stated under the form it belongs to. */
export function FormError({ message }: { message: string }) {
  return (
    <Inline
      role="alert"
      gap="sm"
      align="start"
      className="text-label text-risk"
    >
      <Icon icon={AlertTriangle} size="sm" className="mt-0.5" />
      <Box render={<span />}>{message}</Box>
    </Inline>
  )
}
