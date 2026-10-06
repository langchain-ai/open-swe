import { useQuery } from "@tanstack/react-query"
import { useState } from "react"

import { ConfirmableAction } from "@langchain/gtm-platform-design-system/patterns/confirmable-action"
import { Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"

import { ChevronDown, GitMerge } from "@/components/glyphs"
import { api, type MergeMethod, type OpenPullRequest } from "@/lib/api"
import { expiresInBrowser } from "@/lib/query"
import { actionLabel, githubActions } from "../lib/githubActions"
import {
  mergeMethodLabels,
  mergeMethods,
  readPreferredMergeMethod,
  writePreferredMergeMethod,
} from "../lib/mergeMethod"
import { pullRequestKey } from "../lib/status"
import { usePullRequestAction } from "../lib/usePullRequestAction"

function asMergeMethod(value: unknown): MergeMethod | null {
  return mergeMethods.find((method) => method === value) ?? null
}

export function MergePullRequest({
  pr,
  apply,
  onMerged,
}: {
  pr: OpenPullRequest
  apply: () => () => void
  onMerged?: () => void
}) {
  const [choice, setChoice] = useState<MergeMethod | "">("")
  const [preferred] = useState(readPreferredMergeMethod)
  const allowed = useQuery({
    queryKey: ["repo-merge-methods", pr.repo],
    queryFn: () => api.repoMergeMethods(pr.repo),
    ...expiresInBrowser,
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
    apply,
    onDone: () => {
      if (method) writePreferredMergeMethod(method)
      onMerged?.()
    },
  })
  return (
    <Inline gap="sm" wrap>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={<Button size="compact" variant="outline" />}
          aria-label={`Merge method for PR #${pr.number}`}
          disabled={allowed.isPending || merge.isPending}
        >
          {method ? mergeMethodLabels[method] : "Merge method"}
          <Icon icon={ChevronDown} size="sm" className="text-ink-subtle" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuRadioGroup
            value={method}
            onValueChange={(value: unknown) => {
              const chosen = asMergeMethod(value)
              if (chosen !== null) setChoice(chosen)
            }}
          >
            {options.map((option) => (
              <DropdownMenuRadioItem key={option} value={option} closeOnClick>
                {mergeMethodLabels[option]}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      {/* A merge cannot be taken back, so the button opens the decision; once
          confirmed it still runs in the background like every other action. */}
      <ConfirmableAction
        tone="PRIMARY"
        confirmIcon={GitMerge}
        title={`Merge ${pullRequestKey(pr)}?`}
        description={`GitHub merges it into the base branch${method ? ` as a ${mergeMethodLabels[method].toLowerCase()}` : ""}. A merge cannot be undone from here.`}
        confirmLabel="Merge pull request"
        onConfirm={async () => {
          merge.mutate()
        }}
        trigger={
          <Button
            size="compact"
            variant="outline"
            disabled={
              !method || !pr.headSha || merge.isPending || merge.isSuccess
            }
            aria-live="polite"
          >
            {actionLabel(githubActions.merge.labels, merge)}
          </Button>
        }
      />
    </Inline>
  )
}
