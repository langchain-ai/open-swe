import {
  api,
  type OpenPullRequest,
  type PullRequestThreadResult,
} from "@/lib/api"

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

/** The one button that asks the agent to look the PR over and fix what is clearly broken. */
export const fixUpAction: ThreadAction = {
  labels: {
    idle: "Fix up",
    running: "Fixing up",
    checking: "Checking…",
    unavailable: "Fix up unavailable",
    queued: "Fix up queued",
    retry: "Retry fix up",
  },
  toasts: {
    queued: "Fix up queued for",
    running: "Already fixing up",
    failed: "Could not queue a fix up for",
  },
  run: (pr) => api.fixUpPullRequest(pr),
}
