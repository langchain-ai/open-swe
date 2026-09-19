import {
  api,
  type OpenPullRequest,
  type PullRequestThreadResult,
} from "@/lib/api"

export type PullRequestThreadActionName = "fix" | "address-comments"

export interface ThreadActionLabels {
  idle: string
  running: string
  checking: string
  unavailable: string
  queuing: string
  queued: string
  retry: string
}

export interface ThreadActionToasts {
  queued: string
  running: string
  failed: string
}

export interface ThreadAction {
  labels: ThreadActionLabels
  toasts: ThreadActionToasts
  run: (pr: OpenPullRequest) => Promise<PullRequestThreadResult>
}

export const threadActions: Record<PullRequestThreadActionName, ThreadAction> =
  {
    fix: {
      labels: {
        idle: "Fix",
        running: "Fix in progress",
        checking: "Checking…",
        unavailable: "Fix unavailable",
        queuing: "Queuing fix…",
        queued: "Fix queued",
        retry: "Retry fix",
      },
      toasts: {
        queued: "Fix queued for",
        running: "Fix already in progress for",
        failed: "Could not queue fix for",
      },
      run: (pr) => api.fixPullRequest(pr),
    },
    "address-comments": {
      labels: {
        idle: "Address comments",
        running: "Addressing comments",
        checking: "Checking…",
        unavailable: "Address comments unavailable",
        queuing: "Queuing comment fixes…",
        queued: "Comment fixes queued",
        retry: "Retry address comments",
      },
      toasts: {
        queued: "Queued comment fixes for",
        running: "Already addressing comments for",
        failed: "Could not queue comment fixes for",
      },
      run: (pr) => api.addressPullRequestComments(pr),
    },
  }
