import { useMemo } from "react"
import type { ReactNode } from "react"

import type { WorkflowPushApproval } from "@/features/agents/lib/types"
import {
  agentMutationKeys,
  useWorkflowApprovalDecision,
  useWorkflowApprovals,
} from "@/features/agents/lib/queries"
import {
  Alert,
  AlertDescription,
  AlertTitle,
} from "@langchain/gtm-platform-design-system/ui/alert"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  Collapsible,
  CollapsibleChevron,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@langchain/gtm-platform-design-system/ui/collapsible"
import {
  Frame,
  FramePanel,
} from "@langchain/gtm-platform-design-system/ui/frame"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { IconWell } from "@langchain/gtm-platform-design-system/ui/icon-well"
import { AlertTriangle, GitMerge, ShieldCheck } from "@/components/glyphs"
import { usePendingVariables } from "@/lib/optimistic"

function shortSha(value: string): string {
  return value ? value.slice(0, 7) : "unknown"
}

function pendingApprovals(
  approvals: Array<WorkflowPushApproval> | undefined
): Array<WorkflowPushApproval> {
  return (approvals ?? []).filter((approval) => approval.status === "pending")
}

function fileLabel(count: number): string {
  return count === 1 ? "1 file" : `${count} files`
}

/** A labelled fact: caps label on the left, the value beside it. */
function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Inline gap="md" align="baseline">
      <Box
        render={<span />}
        className="w-40 shrink-0 text-meta font-medium tracking-caps text-ink-subtle uppercase"
      >
        {label}
      </Box>
      <Box render={<span />} className="min-w-0 flex-1 text-label text-ink">
        {children}
      </Box>
    </Inline>
  )
}

export function WorkflowApprovalCard({
  threadId,
  pollWhileActive = false,
}: {
  threadId: string
  pollWhileActive?: boolean
}) {
  const query = useWorkflowApprovals(threadId, { pollWhileActive })
  const decision = useWorkflowApprovalDecision(threadId)
  const pendingDecisions = usePendingVariables<{
    fingerprint: string
    decision: "approve" | "reject"
  }>(agentMutationKeys.workflowDecision(threadId))
  const approvals = useMemo(
    () => pendingApprovals(query.data?.approvals),
    [query.data?.approvals]
  )

  if (approvals.length === 0) return null

  const decide = (approval: WorkflowPushApproval, kind: "approve" | "reject") =>
    decision.mutate({ fingerprint: approval.fingerprint, decision: kind })

  return (
    <Stack gap="md" data-testid="workflow-approval-group" className="w-full">
      {approvals.map((approval) => {
        const pending = pendingDecisions.find(
          (vars) => vars.fingerprint === approval.fingerprint
        )
        const busy = pending !== undefined
        const inherited = approval.inheritedFrom
        return (
          <Frame
            key={approval.fingerprint}
            data-testid="workflow-approval-card"
            className="w-full"
          >
            <Inline gap="md" align="start" className="px-3 pt-3 pb-2">
              <IconWell className="mt-0.5">
                <Icon icon={ShieldCheck} size="sm" className="text-ink" />
              </IconWell>
              <Stack gap="xs" align="start" className="min-w-0 flex-1">
                <Badge tier="quiet" tone="attention">
                  Push paused for review
                </Badge>
                <Box
                  render={<h2 />}
                  className="text-title font-semibold text-ink"
                >
                  {inherited
                    ? `Confirm workflow changes inherited from ${inherited}`
                    : "Confirm GitHub Actions workflow changes"}
                </Box>
                <Box render={<p />} className="text-label text-ink-subtle">
                  {inherited
                    ? `This branch now includes ${fileLabel(approval.files.length)} from merging ${inherited}. Open SWE did not author these workflow changes.`
                    : `Open SWE is ready to push ${fileLabel(approval.files.length)} in .github/workflows.`}
                </Box>
              </Stack>
            </Inline>

            <FramePanel className="gap-3">
              {inherited && (
                <Inline gap="sm" align="start">
                  <Icon
                    icon={GitMerge}
                    size="sm"
                    className="mt-0.5 text-ink-subtle"
                  />
                  <Stack gap="none">
                    <Box
                      render={<p />}
                      className="text-label font-medium text-ink"
                    >
                      Where these changes came from
                    </Box>
                    <Box render={<p />} className="text-meta text-ink-subtle">
                      Merging {inherited} into{" "}
                      {approval.branch || "the current branch"}
                    </Box>
                  </Stack>
                </Inline>
              )}

              <Alert tone="attention" icon={AlertTriangle}>
                <AlertTitle>Why you need to confirm</AlertTitle>
                <AlertDescription>
                  Workflow files control CI jobs and may access repository
                  secrets. Open SWE pauses before pushing any workflow change.
                </AlertDescription>
              </Alert>

              <Stack gap="sm">
                <Fact label="What happens next">
                  The paused push resumes; no other changes are approved.
                </Fact>
                <Fact label="Branch update">
                  <span className="font-mono">
                    {shortSha(approval.baseSha)} → {shortSha(approval.headSha)}
                  </span>
                </Fact>
              </Stack>

              <Collapsible className="rounded-compact border border-line">
                <CollapsibleTrigger className="flex w-full cursor-pointer justify-between gap-3 px-3 py-2 text-label font-medium text-ink">
                  <Inline render={<span />} gap="xs" align="center">
                    <CollapsibleChevron />
                    Review files and diff
                  </Inline>
                  <Inline
                    render={<span />}
                    gap="sm"
                    align="center"
                    className="font-normal text-ink-subtle tabular-nums"
                  >
                    {approval.diffStats.files} files
                    <span className="text-positive">
                      +{approval.diffStats.additions}
                    </span>
                    <span className="text-risk">
                      -{approval.diffStats.deletions}
                    </span>
                  </Inline>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <Stack gap="sm" className="border-t border-line p-3">
                    <Stack
                      render={<ul />}
                      gap="xs"
                      className="font-mono text-meta text-ink-subtle"
                    >
                      {approval.files.map((file) => (
                        <li key={file} className="truncate" title={file}>
                          {file}
                        </li>
                      ))}
                    </Stack>
                    {approval.diffPreview && (
                      <pre className="max-h-72 overflow-auto rounded-compact border border-line bg-muted px-3 py-2.5 font-mono text-meta text-ink">
                        {approval.diffPreview}
                      </pre>
                    )}
                    {approval.diffPreviewTruncated && (
                      <Box
                        render={<p />}
                        className="text-meta text-ink-subtle"
                      >
                        Diff preview is truncated.
                      </Box>
                    )}
                  </Stack>
                </CollapsibleContent>
              </Collapsible>

              <Box
                render={<p />}
                className="font-mono text-meta break-all text-ink-subtle"
              >
                Approval ID: {approval.fingerprint}
              </Box>
            </FramePanel>

            <Inline
              gap="md"
              align="center"
              justify="between"
              wrap
              className="px-3 pt-2 pb-2.5"
            >
              <Box
                render={<p />}
                className="min-w-0 flex-1 basis-60 text-meta text-ink-subtle"
              >
                Approval resumes this exact push only. If the workflow files
                change, Open SWE will ask again.
              </Box>
              <Inline gap="sm" align="center" className="shrink-0">
                <Button
                  variant="outline"
                  disabled={busy}
                  loading={pending?.decision === "reject"}
                  onClick={() => decide(approval, "reject")}
                >
                  Cancel push
                </Button>
                <Button
                  disabled={busy}
                  loading={pending?.decision === "approve"}
                  onClick={() => decide(approval, "approve")}
                >
                  Approve &amp; continue push
                </Button>
              </Inline>
            </Inline>
          </Frame>
        )
      })}
    </Stack>
  )
}
