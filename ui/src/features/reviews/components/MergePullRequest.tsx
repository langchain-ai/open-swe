import { useState } from "react"

import type { MergeMethod, OpenPullRequest } from "@/lib/api"
import { actionLabel, githubActions } from "../lib/githubActions"
import {
  mergeMethodLabels,
  mergeMethods,
  readPreferredMergeMethod,
  writePreferredMergeMethod,
} from "../lib/mergeMethod"
import { control } from "../lib/styles"
import { usePullRequestAction } from "../lib/usePullRequestAction"
import { PullRequestActionButton } from "./PullRequestActionButton"

function asMergeMethod(value: string): MergeMethod | "" | null {
  if (value === "") return ""
  return mergeMethods.find((method) => method === value) ?? null
}

export function MergePullRequest({
  pr,
  onMerged,
}: {
  pr: OpenPullRequest
  onMerged: () => void
}) {
  const [method, setMethod] = useState<MergeMethod | "">(
    () => readPreferredMergeMethod() ?? ""
  )
  const merge = usePullRequestAction({
    pr,
    action: "merge",
    method: method || undefined,
    onDone: () => {
      if (method) writePreferredMergeMethod(method)
      onMerged()
    },
  })
  return (
    <PullRequestActionButton
      label={actionLabel(githubActions.merge.labels, merge)}
      disabled={!method || !pr.headSha || merge.isPending || merge.isSuccess}
      onClick={() => merge.mutate()}
      errors={[merge.error]}
    >
      <select
        className={control}
        aria-label={`Merge method for PR #${pr.number}`}
        value={method}
        disabled={merge.isPending}
        onChange={(event) => {
          const chosen = asMergeMethod(event.target.value)
          if (chosen !== null) setMethod(chosen)
        }}
      >
        <option value="" disabled>
          Merge method
        </option>
        {mergeMethods.map((option) => (
          <option key={option} value={option}>
            {mergeMethodLabels[option]}
          </option>
        ))}
      </select>
    </PullRequestActionButton>
  )
}
