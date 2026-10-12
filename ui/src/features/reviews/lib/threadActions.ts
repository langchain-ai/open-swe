import {
  api,
  type OpenPullRequest,
  type PullRequestThreadResult,
} from "@/lib/api"

export type PullRequestThreadActionName =
  | "fix-conflicts"
  | "fix-checks"
  | "address-comments"

export interface ThreadActionLabels {
  idle: string
  running: string
  checking: string
  unavailable: string
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
    "fix-conflicts": {
      labels: {
        idle: "Fix conflicts",
        running: "Fixing conflicts",
        checking: "Checking…",
        unavailable: "Fix conflicts unavailable",
        queued: "Conflict fix queued",
        retry: "Retry fix conflicts",
      },
      toasts: {
        queued: "Conflict fix queued for",
        running: "Already fixing conflicts for",
        failed: "Could not queue a conflict fix for",
      },
      run: (pr) => api.fixPullRequest(pr, "conflicts"),
    },
    "fix-checks": {
      labels: {
        idle: "Fix checks",
        running: "Fixing checks",
        checking: "Checking…",
        unavailable: "Fix checks unavailable",
        queued: "Check fix queued",
        retry: "Retry fix checks",
      },
      toasts: {
        queued: "Check fix queued for",
        running: "Already fixing checks for",
        failed: "Could not queue a check fix for",
      },
      run: (pr) => api.fixPullRequest(pr, "checks"),
    },
    "address-comments": {
      labels: {
        idle: "Address comments",
        running: "Addressing comments",
        checking: "Checking…",
        unavailable: "Address comments unavailable",
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
