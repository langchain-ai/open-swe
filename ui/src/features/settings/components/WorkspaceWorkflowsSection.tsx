import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"
import { Switch } from "@langchain/macaw-components/Switch"
import { Text } from "@langchain/macaw-components/Text"
import { Typeahead } from "@langchain/macaw-components/Typeahead"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { api, type WorkflowPolicy } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"

const WORKFLOW_FILE = /^[A-Za-z0-9_-][A-Za-z0-9_.-]*\.ya?ml$/

type InputRow = { key: string; value: string }

interface Draft {
  existing: boolean
  repository: string
  workflow: string
  ref: string
  inputs: Array<InputRow>
  allowBotInputs: boolean
  adminOnly: boolean
}

const EMPTY_DRAFT: Draft = {
  existing: false,
  repository: "",
  workflow: "",
  ref: "",
  inputs: [],
  allowBotInputs: false,
  adminOnly: false,
}

function draftFrom(policy: WorkflowPolicy): Draft {
  return {
    existing: true,
    repository: policy.repository,
    workflow: policy.workflow,
    ref: policy.ref,
    inputs: Object.entries(policy.fixed_inputs).map(([key, value]) => ({
      key,
      value,
    })),
    allowBotInputs: policy.allow_bot_inputs,
    adminOnly: policy.admin_only,
  }
}

function policyFrom(draft: Draft): WorkflowPolicy {
  const keys = draft.inputs.map((row) => row.key.trim())
  if (!draft.repository) throw new Error("Choose a repository.")
  if (!WORKFLOW_FILE.test(draft.workflow))
    throw new Error("Choose a workflow file, such as deploy.yml.")
  if (!draft.ref.trim()) throw new Error("Choose the branch or tag to run on.")
  if (keys.some((key) => !key))
    throw new Error("Every fixed input needs a name.")
  if (new Set(keys).size !== keys.length)
    throw new Error("Fixed input names must be unique.")
  return {
    repository: draft.repository,
    workflow: draft.workflow,
    ref: draft.ref.trim(),
    fixed_inputs: Object.fromEntries(
      draft.inputs.map((row) => [row.key.trim(), row.value])
    ),
    allow_bot_inputs: draft.allowBotInputs,
    admin_only: draft.adminOnly,
  }
}

const samePolicy = (a: WorkflowPolicy, b: WorkflowPolicy) =>
  a.repository === b.repository && a.workflow === b.workflow

export function WorkspaceWorkflowsSection({
  slug,
  canEdit,
}: {
  slug: string
  canEdit: boolean
}) {
  const qc = useQueryClient()
  const queryKey = ["workflowPolicies", slug]
  const policies = useQuery({
    queryKey,
    queryFn: () => api.listWorkflowPolicies(slug),
  })
  const repositories = useQuery({
    queryKey: ["workspaceRepositories", slug],
    queryFn: () => api.listWorkspaceRepositories(slug),
  })
  const [draft, setDraft] = useState<Draft | null>(null)
  const [error, setError] = useState("")
  const options = useQuery({
    queryKey: ["workflowOptions", slug, draft?.repository ?? ""],
    queryFn: () => api.workflowOptions(slug, draft?.repository ?? ""),
    enabled: Boolean(draft?.repository),
  })
  const mutation = useMutation({
    mutationFn: async ({
      policy,
      remove,
    }: {
      policy: WorkflowPolicy
      remove: boolean
    }) => {
      if (remove) await api.removeWorkflowPolicy(slug, policy)
      else await api.saveWorkflowPolicy(slug, policy)
    },
    meta: { errorTitle: "Couldn't update workflow permissions" },
    onMutate: async ({ policy, remove }) => ({
      undo: await optimisticUpdate<WorkflowPolicy[]>(
        qc,
        queryKey,
        (current) => {
          const rows = current.filter((row) => !samePolicy(row, policy))
          return remove ? rows : [...rows, policy]
        }
      ),
    }),
    onError: (_error, _vars, context) => context?.undo(),
    onSettled: () => qc.invalidateQueries({ queryKey }),
  })

  const update = (patch: Partial<Draft>) =>
    setDraft((current) => (current ? { ...current, ...patch } : current))
  const open = (next: Draft) => {
    setError("")
    setDraft(next)
  }
  const save = () => {
    if (!draft) return
    let policy: WorkflowPolicy
    try {
      policy = policyFrom(draft)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Check the policy.")
      return
    }
    setDraft(null)
    mutation.mutate({ policy, remove: false })
  }

  const boundRepos = repositories.data ?? []
  const rows = policies.data ?? []

  return (
    <SettingsSection
      title="GitHub Workflows"
      description="Workflows Open SWE may trigger from this workspace. Each runs only on its fixed branch or tag, with its fixed inputs. Requires the GitHub App's Actions: write permission."
      action={
        canEdit && !draft ? (
          <Button
            size="xs"
            color="secondary"
            variant="outlined"
            disabled={boundRepos.length === 0}
            onClick={() => open(EMPTY_DRAFT)}
          >
            Add workflow
          </Button>
        ) : undefined
      }
    >
      {policies.isError ? (
        <div className="flex items-center gap-space-2 px-space-4 py-space-3">
          <p className="text-xs text-error-secondary">
            Couldn't load workflow permissions.
          </p>
          <Button
            size="xs"
            color="secondary"
            variant="plain"
            onClick={() => void policies.refetch()}
          >
            Retry
          </Button>
        </div>
      ) : policies.isPending ? (
        <p className="px-space-4 py-space-3 text-xs text-secondary">Loading…</p>
      ) : rows.length === 0 && !draft ? (
        <p className="px-space-4 py-space-3 text-xs text-secondary">
          {boundRepos.length === 0 && !repositories.isPending
            ? "Bind a repository to this workspace first."
            : "No workflows allowed. Open SWE can't trigger any workflow from this workspace."}
        </p>
      ) : (
        rows.map((policy) => (
          <div
            key={`${policy.repository}/${policy.workflow}`}
            data-testid="workflow-policy"
            className="flex flex-col gap-space-2 px-space-4 py-space-3 sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="flex min-w-0 flex-col gap-space-1">
              <span className="truncate font-mono text-xs text-primary">
                {policy.repository} · {policy.workflow}
              </span>
              <div className="flex flex-wrap items-center gap-space-1">
                <Badge color="secondary" size="xxs">
                  {policy.ref}
                </Badge>
                <Badge color="secondary" size="xxs">
                  {policy.allow_bot_inputs
                    ? "Bot may add inputs"
                    : "Fixed inputs only"}
                </Badge>
                {policy.admin_only && (
                  <Badge color="secondary" size="xxs">
                    Admin threads only
                  </Badge>
                )}
              </div>
            </div>
            {canEdit && (
              <div className="flex shrink-0 gap-space-2">
                <Button
                  size="xs"
                  color="secondary"
                  variant="plain"
                  aria-label={`Edit ${policy.repository} ${policy.workflow}`}
                  disabled={mutation.isPending}
                  onClick={() => open(draftFrom(policy))}
                >
                  Edit
                </Button>
                <Button
                  size="xs"
                  color="secondary"
                  variant="plain"
                  aria-label={`Remove ${policy.repository} ${policy.workflow}`}
                  disabled={mutation.isPending}
                  onClick={() => mutation.mutate({ policy, remove: true })}
                >
                  Remove
                </Button>
              </div>
            )}
          </div>
        ))
      )}
      {draft && (
        <div className="flex flex-col divide-y divide-default bg-surface-level-1">
          <SettingsRow
            label="Repository"
            description="A repository bound to this workspace."
            control={
              <Select
                aria-label="Workflow repository"
                options={boundRepos.map((row) => ({
                  value: row.repo,
                  label: row.repo,
                }))}
                value={draft.repository || undefined}
                onChange={(value) =>
                  update({ repository: value ?? "", workflow: "", ref: "" })
                }
                placeholder="Choose a repository"
                disabled={draft.existing}
                triggerClassName="w-64"
              />
            }
          />
          <SettingsRow
            label="Workflow"
            description="A workflow file in .github/workflows."
            control={
              <Typeahead<string>
                aria-label="Workflow file"
                className="w-64"
                size="sm"
                freeSolo
                forcePopupIcon
                options={options.data?.workflows ?? []}
                emptyText={
                  options.isFetching
                    ? "Loading workflows…"
                    : "No workflows found"
                }
                placeholder="deploy.yml"
                value={draft.workflow || null}
                inputValue={draft.workflow}
                onInputChange={(workflow) => update({ workflow })}
                onChange={(next) => update({ workflow: next ?? "" })}
                disabled={!draft.repository || draft.existing}
              />
            }
          />
          <SettingsRow
            label="Branch or Tag"
            description="The only ref Open SWE can run it on. Prefer a protected branch."
            control={
              <Typeahead<string>
                aria-label="Workflow ref"
                className="w-64"
                size="sm"
                freeSolo
                forcePopupIcon
                options={options.data?.refs ?? []}
                emptyText={
                  options.isFetching
                    ? "Loading refs…"
                    : "No branches or tags found"
                }
                placeholder="main"
                value={draft.ref || null}
                inputValue={draft.ref}
                onInputChange={(ref) => update({ ref })}
                onChange={(next) => update({ ref: next ?? "" })}
                disabled={!draft.repository}
              />
            }
          />
          <div className="flex flex-col gap-space-2 px-space-4 py-space-3">
            <div className="flex items-start justify-between gap-space-4">
              <div className="flex flex-col gap-space-1">
                <Text as="span" variant="md" weight="medium">
                  Fixed Inputs
                </Text>
                <Text as="span" variant="sm" color="secondary">
                  Always sent, and never overridable by Open SWE. Don't put
                  secrets here.
                </Text>
              </div>
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                onClick={() =>
                  update({ inputs: [...draft.inputs, { key: "", value: "" }] })
                }
              >
                Add input
              </Button>
            </div>
            {draft.inputs.map((row, index) => (
              <div key={index} className="flex items-center gap-space-2">
                <Input
                  aria-label={`Input ${index + 1} name`}
                  size="sm"
                  placeholder="name"
                  className="flex-1"
                  value={row.key}
                  debounceMs={0}
                  onChange={(key) =>
                    update({
                      inputs: draft.inputs.map((item, i) =>
                        i === index ? { ...item, key } : item
                      ),
                    })
                  }
                />
                <Input
                  aria-label={`Input ${index + 1} value`}
                  size="sm"
                  placeholder="value"
                  className="flex-1"
                  value={row.value}
                  debounceMs={0}
                  onChange={(value) =>
                    update({
                      inputs: draft.inputs.map((item, i) =>
                        i === index ? { ...item, value } : item
                      ),
                    })
                  }
                />
                <Button
                  size="xs"
                  color="secondary"
                  variant="plain"
                  aria-label={`Remove input ${index + 1}`}
                  onClick={() =>
                    update({
                      inputs: draft.inputs.filter((_, i) => i !== index),
                    })
                  }
                >
                  Remove
                </Button>
              </div>
            ))}
          </div>
          <SettingsRow
            label="Allow Additional Bot Inputs"
            description="Open SWE may send inputs beyond the fixed ones."
            control={
              <Switch
                aria-label="Allow additional bot inputs"
                checked={draft.allowBotInputs}
                onChange={(allowBotInputs) => update({ allowBotInputs })}
              />
            }
          />
          <SettingsRow
            label="Admin Threads Only"
            description="Only an admin, in an admin thread, can trigger it."
            control={
              <Switch
                aria-label="Admin threads only"
                checked={draft.adminOnly}
                onChange={(adminOnly) => update({ adminOnly })}
              />
            }
          />
          <div className="flex flex-col gap-space-2 px-space-4 py-space-3">
            {error && (
              <p role="alert" className="text-xs text-error-secondary">
                {error}
              </p>
            )}
            {options.isError && (
              <p className="text-xs text-secondary">
                Couldn't load workflows and refs from GitHub. You can still type
                them.
              </p>
            )}
            <div className="flex justify-end gap-space-2">
              <Button
                size="xs"
                color="secondary"
                variant="plain"
                onClick={() => setDraft(null)}
              >
                Cancel
              </Button>
              <Button
                size="xs"
                color="primary"
                disabled={mutation.isPending}
                onClick={save}
              >
                {mutation.isPending ? "Saving…" : "Save workflow"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </SettingsSection>
  )
}
