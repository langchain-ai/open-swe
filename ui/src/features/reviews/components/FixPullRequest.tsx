import { api, type OpenPullRequest } from "@/lib/api"
import { PullRequestThreadAction } from "./PullRequestThreadAction"

export function FixPullRequest({
  pr,
  login,
}: {
  pr: OpenPullRequest
  login: string
}) {
  return (
    <PullRequestThreadAction
      pr={pr}
      login={login}
      dispatch={(target) => api.fixPullRequest(target)}
      labels={{
        idle: "Fix",
        running: "Fix in progress",
        checking: "Checking…",
        unavailable: "Fix unavailable",
        queuing: "Queuing fix…",
        queued: "Fix queued",
        retry: "Retry fix",
      }}
      toasts={{
        queued: "Fix queued for",
        running: "Fix already in progress for",
        failed: "Could not queue fix for",
      }}
    />
  )
}
