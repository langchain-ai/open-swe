import {
  api,
  type MergeMethod,
  type OpenPullRequest,
  type PullRequestActionName,
} from "@/lib/api"

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
  succeeded: (subject: string) => string
  failed: (subject: string) => string
  run: (
    pr: OpenPullRequest,
    method?: MergeMethod,
    reason?: string
  ) => Promise<void>
}

export const githubActions: Record<PullRequestActionName, GithubAction> = {
  merge: {
    labels: {
      idle: "Merge",
      pending: "Merging…",
      done: null,
      retry: "Retry merge",
    },
    succeeded: (subject) => `Merged ${subject}`,
    failed: (subject) => `Could not merge ${subject}`,
    run: async (pr, method) => {
      if (!method) throw new Error("Choose a merge method.")
      await api.mergePullRequest(pr, method)
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
    run: async (pr, _method, reason) => {
      await api.closePullRequest(pr, reason)
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
      await api.markPullRequestReady(pr)
    },
  },
}

export function actionLabel(
  labels: ActionLabels,
  state: { isPending: boolean; isSuccess: boolean; isError: boolean }
) {
  if (state.isPending) return labels.pending
  if (state.isSuccess && labels.done) return labels.done
  if (state.isError) return labels.retry
  return labels.idle
}
