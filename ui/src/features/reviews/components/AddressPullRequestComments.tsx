import { api, type OpenPullRequest } from "@/lib/api"
import { PullRequestThreadAction } from "./PullRequestThreadAction"

export function AddressPullRequestComments({
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
      dispatch={(target) => api.addressPullRequestComments(target)}
      labels={{
        idle: "Address comments",
        running: "Addressing comments",
        checking: "Checking…",
        unavailable: "Address comments unavailable",
        queuing: "Queuing comment fixes…",
        queued: "Comment fixes queued",
        retry: "Retry address comments",
      }}
      toasts={{
        queued: "Queued comment fixes for",
        running: "Already addressing comments for",
        failed: "Could not queue comment fixes for",
      }}
    />
  )
}
