import { useEffect, useRef, useState } from "react"

import { api, type OpenPullRequest } from "@/lib/api"
import { actionLabel, githubActions } from "../lib/githubActions"
import { usePullRequestAction } from "../lib/usePullRequestAction"
import { PullRequestActionButton } from "./PullRequestActionButton"

const pollEveryMs = 3_000
const pollForMs = 60_000

export function UpdatePullRequestBranch({
  pr,
  onUpdated,
}: {
  pr: OpenPullRequest
  onUpdated: () => void
}) {
  const [queuedFrom, setQueuedFrom] = useState<string | null>(null)
  const onUpdatedRef = useRef(onUpdated)
  useEffect(() => {
    onUpdatedRef.current = onUpdated
  }, [onUpdated])
  const update = usePullRequestAction({
    pr,
    action: "update-branch",
    onDone: () => setQueuedFrom(pr.headSha),
  })

  // GitHub answers 202 before the merge lands, so wait for the head to move.
  useEffect(() => {
    if (!queuedFrom) return
    let cancelled = false
    const deadline = Date.now() + pollForMs
    const poll = async () => {
      while (!cancelled && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, pollEveryMs))
        try {
          const latest = await api.myPullRequestDetails(pr.repo, pr.number)
          if (latest?.headSha && latest.headSha !== queuedFrom) break
        } catch (error) {
          console.error("Polling the updated branch failed", { error })
        }
      }
      if (!cancelled) onUpdatedRef.current()
    }
    void poll()
    return () => {
      cancelled = true
    }
  }, [queuedFrom, pr.repo, pr.number])

  return (
    <PullRequestActionButton
      label={actionLabel(githubActions["update-branch"].labels, update)}
      disabled={!pr.headSha || update.isPending || update.isSuccess}
      onClick={() => update.mutate()}
      errors={[update.error]}
    />
  )
}
