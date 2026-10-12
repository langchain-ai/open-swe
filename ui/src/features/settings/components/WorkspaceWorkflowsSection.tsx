import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { Select } from "@langchain/macaw-components/Select"
import { Switch } from "@langchain/macaw-components/Switch"

import { SettingsSection } from "@/components/AppShell"
import { api, type WorkflowPolicy } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"

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
  const [repository, setRepository] = useState("")
  const options = useQuery({
    queryKey: ["workflowOptions", slug, repository],
    queryFn: () => api.workflowOptions(slug, repository),
    enabled: Boolean(repository),
  })
  const [workflow, setWorkflow] = useState("")
  const [ref, setRef] = useState("main")
  const [fixedInputs, setFixedInputs] = useState("{}")
  const [allowBotInputs, setAllowBotInputs] = useState(false)
  const [adminOnly, setAdminOnly] = useState(false)
  const [error, setError] = useState("")
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
          const rows = current.filter(
            (row) =>
              row.repository !== policy.repository ||
              row.workflow !== policy.workflow
          )
          return remove ? rows : [...rows, policy]
        }
      ),
    }),
    onError: (_error, _vars, context) => context?.undo(),
    onSettled: () => qc.invalidateQueries({ queryKey }),
  })
  const edit = (policy: WorkflowPolicy) => {
    setRepository(policy.repository)
    setWorkflow(policy.workflow)
    setRef(policy.ref)
    setFixedInputs(JSON.stringify(policy.fixed_inputs, null, 2))
    setAllowBotInputs(policy.allow_bot_inputs)
    setAdminOnly(policy.admin_only)
    setError("")
  }
  const save = () => {
    try {
      const parsed: unknown = JSON.parse(fixedInputs)
      if (
        !parsed ||
        typeof parsed !== "object" ||
        Array.isArray(parsed) ||
        Object.values(parsed).some((value) => typeof value !== "string")
      )
        throw new Error(
          "Fixed inputs must be a JSON object with string values."
        )
      if (
        !repository ||
        !/^[A-Za-z0-9_-][A-Za-z0-9_.-]*\.ya?ml$/.test(workflow) ||
        !ref.trim()
      )
        throw new Error(
          "Choose a repository, workflow filename, and fixed ref."
        )
      const inputs = Object.fromEntries(
        Object.entries(parsed).map(([key, value]) => [key, String(value)])
      )
      setError("")
      mutation.mutate({
        policy: {
          repository,
          workflow,
          ref,
          fixed_inputs: inputs,
          allow_bot_inputs: allowBotInputs,
          admin_only: adminOnly,
        },
        remove: false,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid inputs.")
    }
  }
  return (
    <SettingsSection
      title="GitHub Workflows"
      description="Allow Open SWE to trigger specific workflows in this workspace only. Requires the GitHub App's Actions: write permission. Fixed inputs cannot be overridden; deployment approvals stay in GitHub."
    >
      <div className="flex flex-col gap-space-4 px-space-4 py-space-4">
        {policies.isError ? (
          <p className="text-xs text-error-primary">
            Couldn't load workflow policies.{" "}
            <Button
              size="xs"
              variant="plain"
              color="secondary"
              onClick={() => policies.refetch()}
            >
              Retry
            </Button>
          </p>
        ) : policies.isPending ? (
          <p className="text-xs text-secondary">Loading…</p>
        ) : policies.data.length === 0 ? (
          <p className="text-xs text-secondary">
            No workflows allowed. All dispatches are denied until you add a
            policy.
          </p>
        ) : (
          <ul className="divide-y divide-default">
            {policies.data.map((policy) => (
              <li
                key={`${policy.repository}/${policy.workflow}`}
                className="flex flex-wrap items-center justify-between gap-space-3 py-space-3"
              >
                <div className="flex flex-col gap-space-1">
                  <span className="font-mono text-xs text-primary">
                    {policy.repository} / {policy.workflow}
                  </span>
                  <span className="text-xs text-secondary">
                    Ref: {policy.ref} ·{" "}
                    {policy.allow_bot_inputs
                      ? "Additional bot inputs allowed"
                      : "Fixed inputs only"}
                  </span>
                </div>
                {canEdit && (
                  <div className="flex gap-space-2">
                    <Button
                      size="xs"
                      variant="plain"
                      color="secondary"
                      onClick={() => edit(policy)}
                    >
                      Edit
                    </Button>
                    <Button
                      size="xs"
                      variant="plain"
                      color="secondary"
                      disabled={mutation.isPending}
                      onClick={() => mutation.mutate({ policy, remove: true })}
                    >
                      Remove
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        {canEdit && (
          <div className="flex flex-col gap-space-3 border-t border-default pt-space-4">
            <div className="grid grid-cols-1 gap-space-3 sm:grid-cols-2">
              <div className="flex flex-col gap-space-2">
                <label className="text-xs text-secondary">Repository</label>
                <Select
                  aria-label="Workflow repository"
                  options={(repositories.data ?? []).map((row) => ({
                    value: row.repo,
                    label: row.repo,
                  }))}
                  value={repository}
                  onChange={(value) => setRepository(value ?? "")}
                  placeholder="Choose a bound repository"
                  triggerClassName="w-full"
                />
              </div>
              <Input
                list="workflow-filenames"
                label="Workflow Filename"
                size="sm"
                variant="outlined"
                value={workflow}
                onChange={setWorkflow}
                debounceMs={0}
                placeholder="deploy.yml"
              />
            </div>
            <Input
              list="workflow-refs"
              label="Fixed Ref"
              size="sm"
              value={ref}
              onChange={setRef}
              debounceMs={0}
              hintText="Use a protected branch or tag. Open SWE cannot choose another ref."
            />
            <datalist id="workflow-filenames">
              {options.data?.workflows.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
            <datalist id="workflow-refs">
              {options.data?.refs.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
            {repository && options.isFetching && (
              <p className="text-xs text-secondary">
                Loading workflow and ref suggestions…
              </p>
            )}
            {options.isError && (
              <p className="text-xs text-error-primary">
                Couldn’t load suggestions. You can still enter a filename and
                ref manually.
              </p>
            )}
            <Textarea
              label="Fixed Inputs (JSON)"
              size="sm"
              value={fixedInputs}
              onChange={setFixedInputs}
              rows={3}
              hintText="String values only. Do not put secrets here."
              inputClassName="font-mono"
            />
            <label className="flex items-center gap-space-2 text-xs text-secondary">
              <Switch
                checked={allowBotInputs}
                onChange={setAllowBotInputs}
                aria-label="Allow additional bot inputs"
              />
              Allow additional bot inputs
            </label>
            <label className="flex items-center gap-space-2 text-xs text-secondary">
              <Switch
                checked={adminOnly}
                onChange={setAdminOnly}
                aria-label="Admin threads only"
              />
              Admin threads only
            </label>
            {error && (
              <p role="alert" className="text-xs text-error-primary">
                {error}
              </p>
            )}
            <div>
              <Button
                size="sm"
                color="secondary"
                variant="outlined"
                onClick={save}
                disabled={
                  mutation.isPending || !policies.data || !repositories.data
                }
              >
                Save workflow policy
              </Button>
            </div>
          </div>
        )}
      </div>
    </SettingsSection>
  )
}
