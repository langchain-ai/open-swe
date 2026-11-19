import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"
import { Switch } from "@langchain/macaw-components/Switch"
import { Text } from "@langchain/macaw-components/Text"
import { Textarea } from "@langchain/macaw-components/Textarea"

import { invalidationTopic } from "@/lib/invalidations/topics"
import {
  SettingsPanel,
  SettingsRow,
  SettingsSection,
} from "@/components/AppShell"
import { api, type ExecutionDefaults, type JsonValue } from "@/lib/api"

import { RepoSelector } from "./RepoSelector"
import { SandboxProxySection } from "./WorkspaceProxySection"
import { WorkspaceScriptEditor } from "./WorkspaceScriptEditor"

type SaveExecutionDefaults = (
  changes: Partial<ExecutionDefaults>
) => Promise<ExecutionDefaults>

type ExecutionDefaultsProps = {
  defaults: ExecutionDefaults
  onSave: SaveExecutionDefaults
}

function ExecutionInstructionsSection({
  defaults,
  repositories,
  onSave,
}: ExecutionDefaultsProps & {
  repositories: Array<{
    full_name: string
    private?: boolean
    archived?: boolean
  }>
}) {
  const [prompt, setPrompt] = useState(defaults.prompt)
  const [repos, setRepos] = useState(defaults.repos)
  const dirty =
    prompt !== defaults.prompt ||
    JSON.stringify(repos) !== JSON.stringify(defaults.repos)
  const save = useMutation({
    meta: { silent: true },
    mutationFn: () => onSave({ prompt, repos }),
    onSuccess: (saved) => {
      setPrompt(saved.prompt)
      setRepos(saved.repos)
    },
  })

  return (
    <SettingsSection
      title="Instructions"
      description="Robin-wide instructions and repositories to preload in sandboxes. Preloading does not bind repository ownership or change thread permissions."
    >
      <SettingsPanel>
        <Textarea
          label="Instructions"
          size="md"
          rows={6}
          value={prompt}
          onChange={setPrompt}
          disabled={save.isPending}
          placeholder="Instructions for Robin"
        />
        <div className="flex flex-col gap-space-2">
          <Text as="span" variant="sm" weight="medium">
            Preloaded Repositories
          </Text>
          {repos.length === 0 ? (
            <Text variant="sm" color="secondary">
              No repositories configured.
            </Text>
          ) : (
            <ul className="flex flex-col gap-space-2">
              {repos.map((repo) => (
                <li
                  key={repo}
                  className="flex min-w-0 items-center justify-between gap-space-2"
                >
                  <Text as="span" variant="sm" className="font-mono break-all">
                    {repo}
                  </Text>
                  <Button
                    size="xs"
                    color="secondary"
                    variant="plain"
                    aria-label={`Remove ${repo} from preloaded repositories`}
                    disabled={save.isPending}
                    onClick={() =>
                      setRepos(repos.filter((value) => value !== repo))
                    }
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <RepoSelector
            repos={repositories.filter(
              (repo) => !repos.includes(repo.full_name)
            )}
            placeholder="Add repository to preload"
            emptySelectionLabel="No repository to add"
            disabled={save.isPending}
            onRepoChange={(repo) => {
              if (repo && !repos.includes(repo)) setRepos([...repos, repo])
            }}
          />
        </div>
        {save.error && (
          <Text as="p" variant="sm" color="error" role="alert">
            {save.error.message}
          </Text>
        )}
        <div className="flex justify-end gap-space-2">
          {dirty && (
            <Button
              size="xs"
              color="secondary"
              variant="plain"
              disabled={save.isPending}
              onClick={() => {
                setPrompt(defaults.prompt)
                setRepos(defaults.repos)
              }}
            >
              Cancel
            </Button>
          )}
          <Button
            size="xs"
            color="primary"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving…" : "Save instructions and repositories"}
          </Button>
        </div>
      </SettingsPanel>
    </SettingsSection>
  )
}

function ExecutionSandboxSection({ defaults, onSave }: ExecutionDefaultsProps) {
  const [snapshotId, setSnapshotId] = useState(defaults.snapshot_id ?? "")
  const [snapshotName, setSnapshotName] = useState(defaults.snapshot_name ?? "")
  const [baseSnapshotId, setBaseSnapshotId] = useState(
    defaults.base_snapshot_id ?? ""
  )
  const [vcpus, setVcpus] = useState(
    defaults.vcpus == null ? "" : String(defaults.vcpus)
  )
  const [memory, setMemory] = useState(
    defaults.mem_bytes == null ? "" : String(defaults.mem_bytes / 1024 ** 3)
  )
  const [disk, setDisk] = useState(
    defaults.fs_capacity_bytes == null
      ? ""
      : String(defaults.fs_capacity_bytes / 1024 ** 3)
  )
  const sizes = [
    {
      label: "vCPUs",
      value: vcpus,
      original: defaults.vcpus,
      multiplier: 1,
      set: setVcpus,
    },
    {
      label: "Memory (GiB)",
      value: memory,
      original: defaults.mem_bytes,
      multiplier: 1024 ** 3,
      set: setMemory,
    },
    {
      label: "Disk (GiB)",
      value: disk,
      original: defaults.fs_capacity_bytes,
      multiplier: 1024 ** 3,
      set: setDisk,
    },
  ]
  const dirty =
    snapshotId !== (defaults.snapshot_id ?? "") ||
    snapshotName !== (defaults.snapshot_name ?? "") ||
    baseSnapshotId !== (defaults.base_snapshot_id ?? "") ||
    sizes.some(
      ({ value, original, multiplier }) =>
        value !== (original == null ? "" : String(original / multiplier))
    )
  const save = useMutation({
    meta: { silent: true },
    mutationFn: () => {
      const values = sizes.map(({ label, value, multiplier }) => {
        if (!value.trim()) return null
        const number = Number(value) * multiplier
        if (!Number.isSafeInteger(number) || number <= 0)
          throw new Error(
            `${label} must be positive and resolve to whole bytes or vCPUs.`
          )
        return number
      })
      return onSave({
        snapshot_id: snapshotId.trim() || null,
        snapshot_name: snapshotName.trim() || null,
        base_snapshot_id: baseSnapshotId.trim() || null,
        vcpus: values[0] ?? null,
        mem_bytes: values[1] ?? null,
        fs_capacity_bytes: values[2] ?? null,
      })
    },
    onSuccess: (saved) => {
      setSnapshotId(saved.snapshot_id ?? "")
      setSnapshotName(saved.snapshot_name ?? "")
      setBaseSnapshotId(saved.base_snapshot_id ?? "")
      setVcpus(saved.vcpus == null ? "" : String(saved.vcpus))
      setMemory(
        saved.mem_bytes == null ? "" : String(saved.mem_bytes / 1024 ** 3)
      )
      setDisk(
        saved.fs_capacity_bytes == null
          ? ""
          : String(saved.fs_capacity_bytes / 1024 ** 3)
      )
    },
  })

  return (
    <SettingsSection
      title="Sandbox Configuration"
      description="Snapshot references and sandbox sizing. Leave optional values blank to keep them unset. Saving does not build or rebuild an image."
    >
      <SettingsPanel>
        <div className="grid grid-cols-1 gap-space-3 sm:grid-cols-2">
          {[
            { label: "Snapshot ID", value: snapshotId, set: setSnapshotId },
            {
              label: "Snapshot Name",
              value: snapshotName,
              set: setSnapshotName,
            },
            {
              label: "Base Snapshot ID",
              value: baseSnapshotId,
              set: setBaseSnapshotId,
            },
          ].map(({ label, value, set }) => (
            <Input
              key={label}
              label={label}
              size="md"
              value={value}
              onChange={set}
              debounceMs={0}
              disabled={save.isPending}
            />
          ))}
        </div>
        <div className="grid grid-cols-1 gap-space-3 sm:grid-cols-3">
          {sizes.map(({ label, value, set, multiplier }) => (
            <Input
              key={label}
              label={label}
              size="md"
              type="number"
              min="0"
              step={multiplier === 1 ? "1" : "any"}
              value={value}
              onChange={set}
              debounceMs={0}
              disabled={save.isPending}
            />
          ))}
        </div>
        {save.error && (
          <Text as="p" variant="sm" color="error" role="alert">
            {save.error.message}
          </Text>
        )}
        <div className="flex justify-end gap-space-2">
          {dirty && (
            <Button
              size="xs"
              color="secondary"
              variant="plain"
              disabled={save.isPending}
              onClick={() => {
                setSnapshotId(defaults.snapshot_id ?? "")
                setSnapshotName(defaults.snapshot_name ?? "")
                setBaseSnapshotId(defaults.base_snapshot_id ?? "")
                setVcpus(defaults.vcpus == null ? "" : String(defaults.vcpus))
                setMemory(
                  defaults.mem_bytes == null
                    ? ""
                    : String(defaults.mem_bytes / 1024 ** 3)
                )
                setDisk(
                  defaults.fs_capacity_bytes == null
                    ? ""
                    : String(defaults.fs_capacity_bytes / 1024 ** 3)
                )
              }}
            >
              Cancel
            </Button>
          )}
          <Button
            size="xs"
            color="primary"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving…" : "Save sandbox configuration"}
          </Button>
        </div>
      </SettingsPanel>
    </SettingsSection>
  )
}

function ExecutionScriptsSection({ defaults, onSave }: ExecutionDefaultsProps) {
  const [setupScript, setSetupScript] = useState(defaults.setup_script)
  const [updateScript, setUpdateScript] = useState(defaults.update_script)
  const dirty =
    setupScript !== defaults.setup_script ||
    updateScript !== defaults.update_script
  const save = useMutation({
    meta: { silent: true },
    mutationFn: () =>
      onSave({ setup_script: setupScript, update_script: updateScript }),
    onSuccess: (saved) => {
      setSetupScript(saved.setup_script)
      setUpdateScript(saved.update_script)
    },
  })

  return (
    <SettingsSection
      title="Sandbox Scripts"
      description="Shell scripts for setting up and updating sandboxes. Edit a script, then save the draft here."
    >
      <SettingsPanel>
        <fieldset
          disabled={save.isPending}
          className="flex flex-col gap-space-3"
        >
          {[
            { label: "Setup Script", value: setupScript, set: setSetupScript },
            {
              label: "Update Script",
              value: updateScript,
              set: setUpdateScript,
            },
          ].map(({ label, value, set }) => (
            <div key={label}>
              <Text as="span" variant="sm" weight="medium">
                {label}
              </Text>
              <WorkspaceScriptEditor
                label={label}
                repos={defaults.repos}
                repositoryLabel="Preloaded repositories"
                value={value}
                onChange={set}
              />
            </div>
          ))}
        </fieldset>
        {save.error && (
          <Text as="p" variant="sm" color="error" role="alert">
            {save.error.message}
          </Text>
        )}
        <div className="flex justify-end gap-space-2">
          {dirty && (
            <Button
              size="xs"
              color="secondary"
              variant="plain"
              disabled={save.isPending}
              onClick={() => {
                setSetupScript(defaults.setup_script)
                setUpdateScript(defaults.update_script)
              }}
            >
              Cancel
            </Button>
          )}
          <Button
            size="xs"
            color="primary"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving…" : "Save scripts"}
          </Button>
        </div>
      </SettingsPanel>
    </SettingsSection>
  )
}

const PROXY_PARAMETER_KEYS = [
  "proxy_config",
  "tag_value_ids",
  "access_delegation",
]

function ExecutionParametersSection({
  defaults,
  onSave,
}: ExecutionDefaultsProps) {
  const original = JSON.stringify(
    Object.fromEntries(
      Object.entries(defaults.create_params).filter(
        ([key]) => !PROXY_PARAMETER_KEYS.includes(key)
      )
    ),
    null,
    2
  )
  const [draft, setDraft] = useState(original)
  const save = useMutation({
    meta: { silent: true },
    mutationFn: () => {
      const parsed: JsonValue = JSON.parse(draft)
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
        throw new Error("Additional create parameters must be a JSON object.")
      if (PROXY_PARAMETER_KEYS.some((key) => key in parsed))
        throw new Error(
          "Edit proxy_config, tag_value_ids, and access_delegation in Sandbox Proxy."
        )
      const proxyParams = Object.fromEntries(
        Object.entries(defaults.create_params).filter(([key]) =>
          PROXY_PARAMETER_KEYS.includes(key)
        )
      )
      return onSave({ create_params: { ...proxyParams, ...parsed } })
    },
    onSuccess: (saved) => {
      setDraft(
        JSON.stringify(
          Object.fromEntries(
            Object.entries(saved.create_params).filter(
              ([key]) => !PROXY_PARAMETER_KEYS.includes(key)
            )
          ),
          null,
          2
        )
      )
    },
  })
  const dirty = draft !== original

  return (
    <SettingsSection
      title="Sandbox Parameters"
      description="Additional sandbox creation parameters, such as provider region and mount_config. Configure proxy rules, governed tags, and LangSmith access in Sandbox Proxy. Never enter keys or secrets."
    >
      <SettingsPanel>
        <Textarea
          label="Additional Create Parameters (JSON)"
          size="md"
          rows={8}
          inputClassName="font-mono text-xs"
          spellCheck={false}
          value={draft}
          onChange={setDraft}
          disabled={save.isPending}
        />
        {save.error && (
          <Text as="p" variant="sm" color="error" role="alert">
            {save.error.message}
          </Text>
        )}
        <div className="flex justify-end gap-space-2">
          {dirty && (
            <Button
              size="xs"
              color="secondary"
              variant="plain"
              disabled={save.isPending}
              onClick={() => setDraft(original)}
            >
              Cancel
            </Button>
          )}
          <Button
            size="xs"
            color="primary"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving…" : "Save sandbox parameters"}
          </Button>
        </div>
      </SettingsPanel>
    </SettingsSection>
  )
}

export function ExecutionDefaultsSettings({
  repositories,
}: {
  repositories: Array<{
    full_name: string
    private?: boolean
    archived?: boolean
  }>
}) {
  const queryKey = ["executionDefaults"]
  const qc = useQueryClient()
  const settings = useQuery({
    queryKey,
    queryFn: api.getExecutionDefaults,
    meta: { invalidatedBy: [invalidationTopic("workspaces")] },
  })
  const save = useMutation({
    mutationKey: [...queryKey, "save"],
    scope: { id: JSON.stringify(queryKey) },
    meta: { errorTitle: "Couldn’t save execution defaults" },
    mutationFn: ({
      changes,
      baseCreateParams,
    }: {
      changes: Partial<ExecutionDefaults>
      baseCreateParams: ExecutionDefaults["create_params"]
    }) => {
      if (!changes.create_params) return api.updateExecutionDefaults(changes)
      const createParams = {
        ...(qc.getQueryData<ExecutionDefaults>(queryKey)?.create_params ??
          baseCreateParams),
      }
      for (const key of Object.keys(baseCreateParams)) {
        if (!(key in changes.create_params)) delete createParams[key]
      }
      for (const [key, value] of Object.entries(changes.create_params)) {
        if (JSON.stringify(value) !== JSON.stringify(baseCreateParams[key]))
          createParams[key] = value
      }
      return api.updateExecutionDefaults({
        ...changes,
        create_params: createParams,
      })
    },
    onMutate: async () => {
      await qc.cancelQueries({ queryKey })
    },
    onSuccess: async (saved, { changes }) => {
      qc.setQueryData(queryKey, saved)
      if (changes.enabled !== undefined) {
        await Promise.all([
          qc.invalidateQueries({ queryKey: ["workspace-options"] }),
          qc.invalidateQueries({ queryKey: ["options"] }),
          qc.invalidateQueries({ queryKey: ["workspaceSettings"] }),
        ])
      }
    },
  })

  if (!settings.data) {
    return (
      <SettingsSection title="Execution Defaults">
        <SettingsPanel>
          {settings.error ? (
            <>
              <Text as="p" variant="sm" color="error" role="alert">
                Couldn’t load execution defaults: {settings.error.message}
              </Text>
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                onClick={() => void settings.refetch()}
              >
                Retry
              </Button>
            </>
          ) : (
            <Text as="p" variant="sm" color="secondary" role="status">
              Loading execution defaults…
            </Text>
          )}
        </SettingsPanel>
      </SettingsSection>
    )
  }

  const onSave: SaveExecutionDefaults = (changes) =>
    save.mutateAsync({
      changes,
      baseCreateParams: settings.data.create_params,
    })

  return (
    <>
      <SettingsSection title="Execution Defaults">
        <SettingsRow
          label="Use Robin-wide Defaults"
          description="Use these instructions, sandbox settings, models, and review defaults for all runs. Turning this off restores the saved legacy configuration."
          control={
            <Switch
              aria-label="Use Robin-wide Defaults"
              checked={settings.data.enabled === true}
              disabled={save.isPending}
              onChange={(enabled) =>
                save.mutate({
                  changes: { enabled },
                  baseCreateParams: settings.data.create_params,
                })
              }
            />
          }
        />
        {save.error && save.variables?.changes.enabled !== undefined && (
          <SettingsPanel>
            <p role="alert" className="text-sm text-error-secondary">
              Couldn’t save execution defaults: {save.error.message}
            </p>
          </SettingsPanel>
        )}
      </SettingsSection>
      <ExecutionInstructionsSection
        defaults={settings.data}
        repositories={repositories}
        onSave={onSave}
      />
      <ExecutionSandboxSection defaults={settings.data} onSave={onSave} />
      <ExecutionScriptsSection defaults={settings.data} onSave={onSave} />
      <ExecutionParametersSection defaults={settings.data} onSave={onSave} />
      <SandboxProxySection
        createParams={settings.data.create_params}
        canEdit
        silentErrors
        onSave={async (params) => {
          await onSave({ create_params: params })
        }}
      />
    </>
  )
}
