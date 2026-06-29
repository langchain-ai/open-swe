import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type {
  WorkspaceDeliveryPolicy,
  WorkspaceDeliveryPolicyUpdateBody,
} from "@/lib/api"
import { api } from "@/lib/api"

function splitList(value: string): Array<string> {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
}

function joinList(value: Array<string> | undefined): string {
  return (value ?? []).join(", ")
}

function draftFromConfig(
  config: WorkspaceDeliveryPolicy | undefined
): WorkspaceDeliveryPolicyUpdateBody {
  return {
    active: config?.active ?? true,
    kill_switch: config?.kill_switch ?? false,
    agent_review: config?.gate_policy.agent_review ?? true,
    qa_evidence: config?.gate_policy.qa_evidence ?? true,
    blocking_gates: config?.gate_policy.blocking_gates ?? [],
    advisory_gates: config?.gate_policy.advisory_gates ?? [],
    max_concurrent_runs: config?.run_limits.max_concurrent_runs ?? 1,
    daily_run_budget: config?.run_limits.daily_run_budget ?? 10,
    merge_enabled: config?.merge_policy.enabled ?? false,
    merge_strategy: config?.merge_policy.strategy ?? "squash",
    required_checks: config?.merge_policy.required_checks ?? [],
    delete_branch: config?.merge_policy.delete_branch ?? true,
    target_branch: config?.merge_policy.target_branch ?? "main",
  }
}

export function WorkspaceDeliveryPolicySection({
  projectId,
}: {
  projectId: string
}) {
  const qc = useQueryClient()
  const config = useQuery({
    queryKey: ["workspaceDeliveryPolicy", projectId],
    queryFn: () => api.getWorkspaceDeliveryPolicy(projectId),
  })
  const [draft, setDraft] = useState<WorkspaceDeliveryPolicyUpdateBody>(() =>
    draftFromConfig(undefined)
  )
  const draftRef = useRef(draft)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (config.data) {
      const nextDraft = draftFromConfig(config.data)
      draftRef.current = nextDraft
      setDraft(nextDraft)
    }
  }, [config.data])

  const updateDraft = (
    updater: (
      current: WorkspaceDeliveryPolicyUpdateBody
    ) => WorkspaceDeliveryPolicyUpdateBody
  ) => {
    setDraft((current) => {
      const nextDraft = updater(current)
      draftRef.current = nextDraft
      return nextDraft
    })
  }

  const save = useMutation({
    mutationFn: () =>
      api.saveWorkspaceDeliveryPolicy(projectId, draftRef.current),
    onSuccess: (next) => {
      setError(null)
      const nextDraft = draftFromConfig(next)
      draftRef.current = nextDraft
      setDraft(nextDraft)
      void qc.invalidateQueries({
        queryKey: ["workspaceDeliveryPolicy", projectId],
      })
      void qc.invalidateQueries({ queryKey: ["deliveryProjects"] })
      void qc.invalidateQueries({
        queryKey: ["deliveryProjectReadiness", projectId],
      })
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <SettingsSection
      title="Delivery Policy"
      description="Configure review, QA, run limits, and policy-gated Auto-Merge."
    >
      <div id="delivery-policy" className="divide-y divide-border">
        <SettingsRow
          label="Status"
          description="Enable or stop this workspace for queued delivery."
          control={
            <div className="flex flex-wrap items-center justify-end gap-3">
              <label className="flex items-center gap-2 text-xs">
                <input
                  aria-label="Project active"
                  type="checkbox"
                  checked={draft.active}
                  onChange={(e) =>
                    updateDraft((current) => ({
                      ...current,
                      active: e.target.checked,
                    }))
                  }
                />
                Active
              </label>
              <label className="flex items-center gap-2 text-xs">
                <input
                  aria-label="Kill switch"
                  type="checkbox"
                  checked={draft.kill_switch}
                  onChange={(e) =>
                    updateDraft((current) => ({
                      ...current,
                      kill_switch: e.target.checked,
                    }))
                  }
                />
                Kill switch
              </label>
            </div>
          }
        />
        <SettingsRow
          label="Review gates"
          description="Require agent review and QA proof before a delivery can merge."
          control={
            <div className="flex flex-wrap items-center justify-end gap-3">
              <label className="flex items-center gap-2 text-xs">
                <input
                  aria-label="Require agent review"
                  type="checkbox"
                  checked={draft.agent_review}
                  onChange={(e) =>
                    updateDraft((current) => ({
                      ...current,
                      agent_review: e.target.checked,
                    }))
                  }
                />
                Agent review
              </label>
              <label className="flex items-center gap-2 text-xs">
                <input
                  aria-label="Require QA evidence"
                  type="checkbox"
                  checked={draft.qa_evidence}
                  onChange={(e) =>
                    updateDraft((current) => ({
                      ...current,
                      qa_evidence: e.target.checked,
                    }))
                  }
                />
                QA evidence
              </label>
            </div>
          }
        />
        <SettingsRow
          label="Blocking gates"
          description="Comma-separated checks that block delivery completion."
          control={
            <Input
              aria-label="Blocking gates"
              className="w-full sm:w-96"
              value={joinList(draft.blocking_gates)}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  blocking_gates: splitList(e.target.value),
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Advisory gates"
          description="Comma-separated checks reported as non-blocking advice."
          control={
            <Input
              aria-label="Advisory gates"
              className="w-full sm:w-96"
              value={joinList(draft.advisory_gates)}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  advisory_gates: splitList(e.target.value),
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Run limits"
          description="Bound concurrent and daily automated delivery work."
          control={
            <div className="grid w-full gap-2 sm:w-96 sm:grid-cols-2">
              <Input
                aria-label="Max concurrent runs"
                type="number"
                min={1}
                value={draft.max_concurrent_runs}
                onChange={(e) =>
                  updateDraft((current) => ({
                    ...current,
                    max_concurrent_runs: Number(e.target.value),
                  }))
                }
              />
              <Input
                aria-label="Daily run budget"
                type="number"
                min={1}
                value={draft.daily_run_budget}
                onChange={(e) =>
                  updateDraft((current) => ({
                    ...current,
                    daily_run_budget: Number(e.target.value),
                  }))
                }
              />
            </div>
          }
        />
        <SettingsRow
          label="Auto-Merge"
          description="Allow merge only after required checks and review policy pass."
          control={
            <div className="flex flex-wrap items-center justify-end gap-3">
              <Badge variant={draft.merge_enabled ? "default" : "destructive"}>
                {draft.merge_enabled ? "Enabled" : "Blocked"}
              </Badge>
              <label className="flex items-center gap-2 text-xs">
                <input
                  aria-label="Enable policy-gated Auto-Merge"
                  type="checkbox"
                  checked={draft.merge_enabled}
                  onChange={(e) =>
                    updateDraft((current) => ({
                      ...current,
                      merge_enabled: e.target.checked,
                    }))
                  }
                />
                Enable
              </label>
            </div>
          }
        />
        <SettingsRow
          label="Merge strategy"
          description="Strategy used by automated pull-request merges."
          control={
            <select
              aria-label="Merge strategy"
              className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs sm:w-60"
              value={draft.merge_strategy}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  merge_strategy: e.target.value,
                }))
              }
            >
              <option value="squash">squash</option>
              <option value="merge">merge</option>
              <option value="rebase">rebase</option>
            </select>
          }
        />
        <SettingsRow
          label="Target branch"
          description="Base branch used when completing delivery pull requests."
          control={
            <Input
              aria-label="Merge target branch"
              className="w-full sm:w-60"
              value={draft.target_branch}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  target_branch: e.target.value,
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Required checks"
          description="Comma-separated status checks required before Auto-Merge."
          control={
            <Input
              aria-label="Required merge checks"
              className="w-full sm:w-96"
              value={joinList(draft.required_checks)}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  required_checks: splitList(e.target.value),
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Delete branch"
          description="Remove delivery branches after a successful merge."
          control={
            <input
              aria-label="Delete branch after merge"
              type="checkbox"
              checked={draft.delete_branch}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  delete_branch: e.target.checked,
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Actions"
          description="Save policy and refresh workspace readiness."
          control={
            <Button
              size="sm"
              onClick={() => save.mutate()}
              disabled={save.isPending || config.isLoading}
            >
              Save policy
            </Button>
          }
        />
      </div>
      {error && <p className="px-4 pb-3 text-xs text-destructive">{error}</p>}
    </SettingsSection>
  )
}
