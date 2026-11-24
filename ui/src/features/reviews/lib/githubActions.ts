import {
  api,
  type MergeMethod,
  type OpenPullRequest,
  type PullRequestActionName,
  type PullRequestActionResult,
} from "@/lib/api"
import { mergesLater } from "./status"

export interface ActionLabels {
  idle: string
  pending: string
  /** Null where a success takes the button off the screen with the row. */
  done: string | null
  retry: string
}

export interface GithubAction {
  labels: ActionLabels
  /** Past tense, applied to one PR reference or to a counted phrase. */
  succeeded: (subject: string, result: PullRequestActionResult) => string
  failed: (subject: string) => string
  run: (
    pr: OpenPullRequest,
    input: GithubActionInput
  ) => Promise<PullRequestActionResult>
}

export interface GithubActionInput {
  method?: MergeMethod
  reason?: string
  /** Resolve every open conversation before merging. */
  resolveConversations?: boolean
  bypassRules?: boolean
}

export const githubActions: Record<PullRequestActionName, GithubAction> = {
  merge: {
    labels: {
      idle: "Merge",
      pending: "Merging…",
      done: null,
      retry: "Retry merge",
    },
    succeeded: (subject, result) =>
      result.in_merge_queue
        ? `Queued ${subject}`
        : result.auto_merge
          ? `Auto-merge enabled for ${subject}`
          : `Merged ${subject}`,
    failed: (subject) => `Could not merge ${subject}`,
    run: async (pr, { method, resolveConversations = false, bypassRules }) => {
      if (!method) throw new Error("Choose a merge method.")
      return api.mergePullRequest(
        pr,
        method,
        resolveConversations && !bypassRules,
        !bypassRules && mergesLater(pr),
        bypassRules ?? false
      )
    },
  },
  "disable-auto-merge": {
    labels: {
      idle: "Disable auto-merge",
      pending: "Disabling auto-merge…",
      done: "Auto-merge disabled",
      retry: "Retry disabling auto-merge",
    },
    succeeded: (subject) => `Auto-merge disabled for ${subject}`,
    failed: (subject) => `Could not disable auto-merge for ${subject}`,
    run: async (pr) => {
      return api.disablePullRequestAutoMerge(pr)
    },
  },
  close: {
    labels: {
      idle: "Close",
      pending: "Closing…",
      done: null,
      retry: "Retry close",
    },
    succeeded: (subject) => `Closed ${subject}`,
    failed: (subject) => `Could not close ${subject}`,
    run: async (pr, { reason }) => {
      return api.closePullRequest(pr, reason)
    },
  },
  "update-branch": {
    labels: {
      idle: "Update branch",
      pending: "Updating branch…",
      done: "Branch update queued",
      retry: "Retry update branch",
    },
    succeeded: (subject) => `Merging the base branch into ${subject}`,
    failed: (subject) => `Could not update ${subject}`,
    run: async (pr) => {
      return api.updatePullRequestBranch(pr)
    },
  },
  "mark-ready": {
    labels: {
      idle: "Mark ready",
      pending: "Marking ready…",
      done: "Marked ready",
      retry: "Retry mark ready",
    },
    succeeded: (subject) => `Marked ${subject} ready for review`,
    failed: (subject) => `Could not mark ${subject} ready`,
    run: async (pr) => {
      return api.markPullRequestReady(pr)
    },
  },
}

export function actionLabel(
  labels: ActionLabels,
  state: { isPending: boolean; isSuccess: boolean; isError: boolean }
) {
  if (state.isPending || state.isSuccess) return labels.done ?? labels.pending
  if (state.isError) return labels.retry
  return labels.idle
}
