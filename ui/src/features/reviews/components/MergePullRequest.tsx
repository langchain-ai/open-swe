import { useQuery } from "@tanstack/react-query"
import { useState } from "react"

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { api, type MergeMethod, type OpenPullRequest } from "@/lib/api"
import { actionLabel, githubActions } from "../lib/githubActions"
import {
  mergeMethodLabels,
  mergeMethods,
  readPreferredMergeMethod,
  writePreferredMergeMethod,
} from "../lib/mergeMethod"
import { usePullRequestAction } from "../lib/usePullRequestAction"
import { PullRequestActionButton } from "./PullRequestActionButton"

export function MergePullRequest({
  pr,
  onMerged,
}: {
  pr: OpenPullRequest
  onMerged: () => void
}) {
  const [choice, setChoice] = useState<MergeMethod | "">("")
  const [preferred] = useState(readPreferredMergeMethod)
  const allowed = useQuery({
    queryKey: ["repo-merge-methods", pr.repo],
    queryFn: () => api.repoMergeMethods(pr.repo),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
  // An unread settings list is an ambiguity, not a known impossibility, so the
  // attempt stands and GitHub's refusal is the answer.
  const options: readonly MergeMethod[] = allowed.isError
    ? mergeMethods
    : mergeMethods.filter((option) =>
        (allowed.data?.mergeMethods ?? []).includes(option)
      )
  const method =
    choice && options.includes(choice)
      ? choice
      : options.length === 1
        ? options[0]!
        : (options.find((option) => option === preferred) ?? "")
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
      <Select<MergeMethod>
        items={mergeMethodLabels}
        value={method || null}
        disabled={allowed.isPending || merge.isPending}
        onValueChange={(chosen) => {
          if (chosen !== null) setChoice(chosen)
        }}
      >
        <SelectTrigger
          size="sm"
          aria-label={`Merge method for PR #${pr.number}`}
        >
          <SelectValue placeholder="Merge method" />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {mergeMethodLabels[option]}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </PullRequestActionButton>
  )
}
