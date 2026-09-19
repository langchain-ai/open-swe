import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import {
  api,
  ApiError,
  type PolicyDefinition,
  type PolicySettingsView,
} from "@/lib/api"

const policyQueryKey = (repository: string | null) =>
  ["review-approval-policy", repository] as const

const lines = (value: string): string[] =>
  value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean)

function initialDraft(view: PolicySettingsView): PolicyDefinition {
  if (view.policy) return view.policy
  return {
    rules: { ...view.shared_policy.rules },
    criteria_markdown: view.repository
      ? ""
      : view.shared_policy.criteria_markdown,
  }
}

function mutationError(error: Error): string {
  if (error instanceof ApiError && error.status === 409) {
    return "This policy changed since you loaded it. Reload before saving again."
  }
  if (error instanceof ApiError && error.status === 422) {
    try {
      const detail: unknown = JSON.parse(error.message)
      if (Array.isArray(detail)) {
        const messages = detail
          .map((item) => {
            if (typeof item !== "object" || item === null || !("msg" in item)) {
              return null
            }
            return typeof item.msg === "string" ? item.msg.slice(0, 200) : null
          })
          .filter((message): message is string => message !== null)
          .slice(0, 3)
        if (messages.length > 0) return messages.join(" ")
      }
    } catch {
      // Fall through to bounded validation guidance.
    }
    return "Review the policy fields and criteria, then try again."
  }
  return error.message || "Could not save the policy."
}

export function ApprovalPolicyPanel({
  repository,
  onDirtyChange,
}: {
  repository: string | null
  onDirtyChange?: (dirty: boolean) => void
}) {
  const [notice, setNotice] = useState<string | null>(null)
  const query = useQuery({
    queryKey: policyQueryKey(repository),
    queryFn: () => api.getReviewApprovalPolicy(repository),
  })

  return (
    <div className="space-y-6 p-4">
      {query.isLoading && <Skeleton className="h-96 w-full" />}
      {query.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load the approval policy. {query.error.message}
        </p>
      )}
      {query.data && (
        <PolicyEditor
          key={repository ?? "shared"}
          repository={repository}
          view={query.data}
          onNotice={setNotice}
          onDirtyChange={onDirtyChange}
        />
      )}
      {notice && (
        <p role="status" className="text-xs text-muted-foreground">
          {notice}
        </p>
      )}
    </div>
  )
}

function PolicyEditor({
  repository,
  view,
  onNotice,
  onDirtyChange,
}: {
  repository: string | null
  view: PolicySettingsView
  onNotice: (notice: string | null) => void
  onDirtyChange?: (dirty: boolean) => void
}) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState(() => initialDraft(view))
  const [requiredChecks, setRequiredChecks] = useState(() =>
    initialDraft(view).rules.required_checks.join("\n")
  )
  const [protectedPaths, setProtectedPaths] = useState(() =>
    initialDraft(view).rules.human_review_paths.join("\n")
  )
  const [baselineVersion, setBaselineVersion] = useState(view.effective_version)
  const baselineVersionRef = useRef(view.effective_version)
  const [error, setError] = useState<string | null>(null)
  const [reloading, setReloading] = useState(false)
  const [dirty, setDirty] = useState(false)
  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange])

  const adoptView = (nextView: PolicySettingsView) => {
    const nextDraft = initialDraft(nextView)
    setDraft(nextDraft)
    setRequiredChecks(nextDraft.rules.required_checks.join("\n"))
    setProtectedPaths(nextDraft.rules.human_review_paths.join("\n"))
    setBaselineVersion(nextView.effective_version)
    baselineVersionRef.current = nextView.effective_version
    setError(null)
    setDirty(false)
  }

  const save = useMutation({
    mutationFn: (policy: PolicyDefinition | null) =>
      api.saveReviewApprovalPolicy(
        repository,
        policy,
        baselineVersionRef.current
      ),
    onSuccess: (saved, policy) => {
      adoptView(saved)
      queryClient.setQueryData(policyQueryKey(repository), saved)
      setError(null)
      onNotice(
        policy
          ? "Policy saved."
          : repository
            ? "Repository policy reset to the shared default."
            : "Policy reset to the built-in default."
      )
    },
    onError: (mutationFailure) => {
      setError(mutationError(mutationFailure))
      onNotice(null)
    },
  })

  const reloadPolicy = async () => {
    setReloading(true)
    try {
      const latest = await api.getReviewApprovalPolicy(repository)
      queryClient.setQueryData(policyQueryKey(repository), latest)
      adoptView(latest)
      onNotice("Policy reloaded.")
      setReloading(false)
    } catch (reloadError) {
      setError(
        reloadError instanceof Error
          ? mutationError(reloadError)
          : "Could not reload the policy."
      )
      setReloading(false)
    }
  }

  const updateRules = (patch: Partial<PolicyDefinition["rules"]>) => {
    setDraft((current) => {
      const next = {
        ...current,
        rules: { ...current.rules, ...patch },
      }
      return next
    })
    setError(null)
    setDirty(true)
    onNotice(null)
  }

  return (
    <div className="space-y-6">
      <section className="space-y-2 rounded-md border border-border bg-muted/20 p-4 text-xs">
        <p>
          Current version:{" "}
          <code title={view.effective_version}>
            {view.effective_version.slice(0, 12)}
          </code>
        </p>
        {view.updated_by && view.updated_at && (
          <p className="text-muted-foreground">
            Saved by {view.updated_by} on{" "}
            {new Date(view.updated_at).toLocaleString()}.
          </p>
        )}
        <p className="text-muted-foreground">
          Shadow mode evaluates whether a pull request could be approved, but
          does not approve or merge it.
        </p>
        {view.effective_version !== baselineVersion && (
          <p className="font-medium">
            A newer policy is available. Reload to replace this draft.
          </p>
        )}
        {!view.can_edit && (
          <p className="font-medium">
            You have read-only access to this policy.
          </p>
        )}
      </section>

      {repository && <RepositoryPolicyContext view={view} />}

      <fieldset
        disabled={!view.can_edit || save.isPending || reloading}
        className="space-y-5"
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="max-risk">Maximum risk score</Label>
            <Input
              id="max-risk"
              type="number"
              min={1}
              max={5}
              value={draft.rules.max_risk_score}
              onChange={(event) =>
                updateRules({ max_risk_score: Number(event.target.value) })
              }
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="minimum-confidence">Minimum confidence</Label>
            <select
              id="minimum-confidence"
              className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
              value={draft.rules.minimum_confidence}
              onChange={(event) =>
                updateRules({
                  minimum_confidence: event.target.value as
                    | "low"
                    | "medium"
                    | "high",
                })
              }
            >
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="required-checks">Required checks</Label>
            <Textarea
              id="required-checks"
              rows={5}
              value={requiredChecks}
              placeholder="One check name per line"
              onChange={(event) => {
                setRequiredChecks(event.target.value)
                setDirty(true)
                setError(null)
                onNotice(null)
              }}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="protected-paths">Protected paths</Label>
            <Textarea
              id="protected-paths"
              rows={5}
              value={protectedPaths}
              placeholder="One path pattern per line"
              onChange={(event) => {
                setProtectedPaths(event.target.value)
                setDirty(true)
                setError(null)
                onNotice(null)
              }}
            />
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="criteria">Additional criteria (Markdown)</Label>
          <Textarea
            id="criteria"
            rows={12}
            className="font-mono text-xs"
            value={draft.criteria_markdown}
            placeholder="## Criterion name"
            onChange={(event) => {
              const criteria_markdown = event.target.value
              setDraft((current) => {
                const next = { ...current, criteria_markdown }
                return next
              })
              setDirty(true)
              setError(null)
              onNotice(null)
            }}
          />
          <p className="text-xs text-muted-foreground">
            Shared criteria require 1–20 named ## sections. Repository criteria
            may be empty.
          </p>
        </div>
      </fieldset>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          disabled={!view.can_edit || save.isPending || reloading}
          onClick={() =>
            save.mutate({
              ...draft,
              rules: {
                ...draft.rules,
                required_checks: lines(requiredChecks),
                human_review_paths: lines(protectedPaths),
              },
            })
          }
        >
          {save.isPending ? "Saving…" : "Save policy"}
        </Button>
        <Button
          variant="outline"
          disabled={
            !view.can_edit || !view.policy || save.isPending || reloading
          }
          onClick={() => save.mutate(null)}
        >
          Reset policy
        </Button>
        <Button
          variant="ghost"
          disabled={reloading || save.isPending}
          onClick={() => void reloadPolicy()}
        >
          {reloading ? "Reloading…" : "Reload policy"}
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

function RepositoryPolicyContext({ view }: { view: PolicySettingsView }) {
  return (
    <section className="space-y-3 rounded-md border border-border p-4 text-xs">
      <p className="font-medium">
        Repository requirements can only make the shared policy stricter.
      </p>
      <details className="rounded-md border border-border p-3">
        <summary className="cursor-pointer font-medium">
          Inherited shared policy (read-only)
        </summary>
        <div className="mt-3 space-y-2 text-muted-foreground">
          <p>
            Maximum risk {view.shared_policy.rules.max_risk_score}; minimum{" "}
            {view.shared_policy.rules.minimum_confidence} confidence.
          </p>
          <p>
            Required checks:{" "}
            {view.shared_policy.rules.required_checks.join(", ") || "none"}
          </p>
          <p>
            Protected paths:{" "}
            {view.shared_policy.rules.human_review_paths.join(", ") || "none"}
          </p>
          <div>
            <p className="font-medium text-foreground">Additional criteria</p>
            <pre className="mt-1 font-sans whitespace-pre-wrap">
              {view.shared_policy.criteria_markdown}
            </pre>
          </div>
        </div>
      </details>
      <div>
        <p className="font-medium">Effective constraints</p>
        <p className="mt-1 text-muted-foreground">
          Effective: maximum risk {view.effective_rules.max_risk_score};{" "}
          {view.effective_rules.minimum_confidence} confidence.
        </p>
        <p className="text-muted-foreground">
          Required checks:{" "}
          {view.effective_rules.required_checks.join(", ") || "none"}
        </p>
        <p className="text-muted-foreground">
          Protected paths:{" "}
          {view.effective_rules.human_review_paths.join(", ") || "none"}
        </p>
      </div>
    </section>
  )
}
