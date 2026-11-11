import { WarningRegularIcon } from "@langchain/macaw-components/icons"
import { Text } from "@langchain/macaw-components/Text"
import { Button } from "@langchain/macaw-components/Button"
import { GitMergeIcon } from "@phosphor-icons/react/dist/ssr/GitMerge"
import { ShieldCheckIcon } from "@phosphor-icons/react/dist/ssr/ShieldCheck"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { CaretDownIcon } from "@phosphor-icons/react/dist/ssr/CaretDown"
import { CaretUpIcon } from "@phosphor-icons/react/dist/ssr/CaretUp"
import { useMemo, useState } from "react"

import type { WorkflowPushApproval } from "@/features/agents/lib/types"
import {
  agentMutationKeys,
  useWorkflowApprovalDecision,
  useWorkflowApprovals,
} from "@/features/agents/lib/queries"
import { usePendingVariables } from "@/lib/optimistic"
import { cn } from "@/lib/utils"

function shortSha(value: string): string {
  return value ? value.slice(0, 7) : "unknown"
}

function latestApprovals(
  approvals: Array<WorkflowPushApproval> | undefined
): Array<WorkflowPushApproval> {
  const latest = new Map<string, WorkflowPushApproval>()
  for (const approval of approvals ?? []) {
    const key = JSON.stringify([approval.repo, approval.branch])
    const current = latest.get(key)
    if (
      !current ||
      (Date.parse(approval.requestedAt ?? "") || 0) >
        (Date.parse(current.requestedAt ?? "") || 0)
    ) {
      latest.set(key, approval)
    }
  }
  return [...latest.values()]
}

function fileLabel(count: number): string {
  return count === 1 ? "1 file" : `${count} files`
}

export function WorkflowApprovalCard({
  threadId,
  pollWhileActive = false,
  latestUserMessageAt,
}: {
  threadId: string
  pollWhileActive?: boolean
  latestUserMessageAt?: string
}) {
  const [expandedCards, setExpandedCards] = useState<Record<string, boolean>>(
    {}
  )
  const query = useWorkflowApprovals(threadId, { pollWhileActive })
  const decision = useWorkflowApprovalDecision(threadId)
  const pendingDecisions = usePendingVariables<{ fingerprint: string }>(
    agentMutationKeys.workflowDecision(threadId)
  )
  const approvals = useMemo(
    () => latestApprovals(query.data?.approvals),
    [query.data?.approvals]
  )

  if (approvals.length === 0) return null

  const decide = (approval: WorkflowPushApproval, kind: "approve" | "reject") =>
    decision.mutate({ fingerprint: approval.fingerprint, decision: kind })

  return (
    <div
      data-testid="workflow-approval-group"
      className="mt-space-4 flex w-full flex-col gap-space-3"
    >
      {approvals.map((approval) => {
        if (approval.status !== "pending") {
          return (
            <section
              key={approval.fingerprint}
              data-testid="workflow-approval-card"
              className="flex items-center gap-space-2 rounded-xl bg-surface-level-1 p-space-3"
            >
              <ShieldCheckIcon
                size={16}
                weight="regular"
                className="text-icon-brand"
              />
              <Text as="span" variant="sm" color="secondary">
                Workflow changes{" "}
                {approval.status === "approved" ? "approved" : "rejected"}
              </Text>
              <span className="truncate text-xs text-secondary">
                {approval.branch}
              </span>
            </section>
          )
        }
        const busy = pendingDecisions.some(
          (vars) => vars.fingerprint === approval.fingerprint
        )
        const inherited = approval.inheritedFrom
        const continued = Boolean(
          latestUserMessageAt &&
          approval.requestedAt &&
          Date.parse(latestUserMessageAt) > Date.parse(approval.requestedAt)
        )
        const expansionKey = `${threadId}:${approval.fingerprint}:${latestUserMessageAt ?? ""}`
        const expanded = expandedCards[expansionKey] ?? !continued
        const toggle = () =>
          setExpandedCards((previous) => ({
            ...previous,
            [expansionKey]: !expanded,
          }))
        return (
          <section
            key={approval.fingerprint}
            data-testid="workflow-approval-card"
            className="overflow-hidden rounded-xl border border-subtle bg-surface-level-1 shadow-sm dark:shadow-none"
          >
            {!expanded && (
              <Button
                color="secondary"
                variant="plain"
                size="sm"
                aria-label={`Expand workflow approval: push paused for review, ${fileLabel(approval.files.length)}, +${approval.diffStats.additions} -${approval.diffStats.deletions}`}
                aria-expanded={false}
                onClick={toggle}
                className="h-auto w-full justify-start gap-space-2 border-0 p-space-3 text-left"
              >
                <ShieldCheckIcon
                  size={16}
                  weight="regular"
                  className="shrink-0 text-icon-brand"
                />
                <span className="min-w-0 flex-1 truncate text-sm text-primary">
                  Workflow changes
                </span>
                <span className="text-xs text-secondary">
                  Push paused for review
                </span>
                <span className="hidden text-xs text-secondary sm:inline">
                  {fileLabel(approval.files.length)}
                </span>
                <span className="hidden text-xs text-success-secondary sm:inline">
                  +{approval.diffStats.additions}
                </span>
                <span className="hidden text-xs text-error-secondary sm:inline">
                  -{approval.diffStats.deletions}
                </span>
                <CaretDownIcon
                  size={16}
                  weight="regular"
                  className="shrink-0"
                />
              </Button>
            )}
            <div hidden={!expanded} className="p-space-4">
              <div className="flex items-start gap-space-3">
                <ShieldCheckIcon
                  size={20}
                  weight="regular"
                  className="mt-0.5 shrink-0 text-icon-brand"
                />
                <div className="min-w-0">
                  <p className="text-xxs font-semibold tracking-wider text-brand-primary uppercase">
                    Push paused for review
                  </p>
                  <Text
                    as="h2"
                    variant="h3"
                    color="primary"
                    className="mt-space-1"
                  >
                    {inherited
                      ? `Confirm workflow changes inherited from ${inherited}`
                      : "Confirm GitHub Actions workflow changes"}
                  </Text>
                  <p className="mt-space-1 text-sm text-secondary">
                    {inherited
                      ? `This branch now includes ${fileLabel(approval.files.length)} from merging ${inherited}. Robin did not author these workflow changes.`
                      : `Robin is ready to push ${fileLabel(approval.files.length)} in .github/workflows.`}
                  </p>
                </div>
                <IconButton
                  icon={CaretUpIcon}
                  label="Collapse workflow approval"
                  color="secondary"
                  variant="plain"
                  size="sm"
                  aria-expanded={true}
                  onClick={toggle}
                  className="ml-auto shrink-0"
                />
              </div>

              {approval.coordinatorStatus && (
                <Text
                  as="p"
                  variant="sm"
                  color="secondary"
                  className="mt-space-3"
                >
                  Workflow changes {approval.coordinatorStatus} on parent. This
                  worker still needs separate approval.
                </Text>
              )}

              {inherited && (
                <div className="mt-space-4 flex gap-space-3 rounded-md border border-brand-subtle bg-brand-muted p-space-3">
                  <GitMergeIcon
                    size={16}
                    weight="regular"
                    className="mt-0.5 shrink-0 text-icon-brand"
                  />
                  <div>
                    <p className="text-xs font-medium text-primary">
                      Where these changes came from
                    </p>
                    <p className="mt-0.5 text-xs text-secondary">
                      Merging {inherited} into{" "}
                      {approval.branch || "the current branch"}
                    </p>
                  </div>
                </div>
              )}

              <div className="mt-space-3 flex gap-space-3 border-l-2 border-warning bg-warning p-space-3">
                <WarningRegularIcon
                  size={16}
                  className="mt-0.5 shrink-0 text-icon-warning"
                />
                <div>
                  <p className="text-xs font-medium text-primary">
                    Why you need to confirm
                  </p>
                  <p className="mt-0.5 text-xs text-secondary">
                    Workflow files control CI jobs and may access repository
                    secrets. Robin pauses before pushing any workflow change.
                  </p>
                </div>
              </div>

              <div className="mt-space-4 flex flex-wrap gap-space-2">
                <Button
                  size="md"
                  disabled={busy}
                  onClick={() => decide(approval, "approve")}
                >
                  Approve &amp; continue push
                </Button>
                <Button
                  size="md"
                  color="secondary"
                  variant="outlined"
                  disabled={busy}
                  onClick={() => decide(approval, "reject")}
                >
                  Cancel push
                </Button>
              </div>
              <p className="mt-space-2 text-xxs text-secondary">
                Approval covers this workflow diff on branches in this thread
                and repository. Different workflow diffs and lease-protected
                pushes require separate approval.
              </p>

              <div className="mt-space-4 grid gap-space-2 sm:grid-cols-2">
                <div className="rounded-md border border-default bg-surface-level-1 p-space-3">
                  <p className="text-xxs font-medium tracking-wide text-secondary uppercase">
                    What happens next
                  </p>
                  <p className="mt-space-1 text-xs text-primary">
                    The paused push resumes; only matching workflow diffs are
                    approved.
                  </p>
                </div>
                <div className="rounded-md border border-default bg-surface-level-1 p-space-3">
                  <p className="text-xxs font-medium tracking-wide text-secondary uppercase">
                    Branch update
                  </p>
                  <p className="mt-space-1 font-mono text-xs text-primary">
                    {shortSha(approval.baseSha)} → {shortSha(approval.headSha)}
                  </p>
                </div>
              </div>

              <details className="mt-space-4 rounded-md border border-default">
                <summary className="flex cursor-pointer items-center justify-between gap-space-3 p-space-3 text-xs font-medium text-primary">
                  <span>Review files and diff</span>
                  <span className="font-normal text-secondary">
                    {approval.diffStats.files} files
                    <span className="ml-space-2 text-success-secondary">
                      +{approval.diffStats.additions}
                    </span>
                    <span className="ml-space-2 text-error-secondary">
                      -{approval.diffStats.deletions}
                    </span>
                  </span>
                </summary>
                <div className="border-t border-default p-space-3">
                  <ul className="space-y-space-1 text-xs text-secondary">
                    {approval.files.map((file) => (
                      <li
                        key={file}
                        className="truncate font-mono"
                        title={file}
                      >
                        {file}
                      </li>
                    ))}
                  </ul>
                  {approval.diffPreview && (
                    <pre
                      className={cn(
                        "mt-space-3 max-h-72 overflow-auto rounded-md border border-default",
                        "bg-surface-level-1 p-space-3 text-xxs leading-relaxed text-primary"
                      )}
                    >
                      {approval.diffPreview}
                    </pre>
                  )}
                  {approval.diffPreviewTruncated && (
                    <p className="mt-space-2 text-xxs text-secondary">
                      Diff preview is truncated.
                    </p>
                  )}
                </div>
              </details>

              <p className="mt-space-3 font-mono text-xxs break-all text-secondary">
                Approval ID: {approval.fingerprint}
              </p>
            </div>
          </section>
        )
      })}
    </div>
  )
}
