import { CheckIcon } from "@langchain/macaw-components/icons"
import { useQuery } from "@tanstack/react-query"
import { useState } from "react"

import { DropdownMenuItem } from "@/components/MenuItem"
import { api, type MergeMethod, type OpenPullRequest } from "@/lib/api"
import { expiresInBrowser } from "@/lib/query"
import { SplitButton } from "@/components/SplitButton"
import { actionLabel, githubActions } from "../lib/githubActions"
import {
  mergeMethodCopy,
  mergeMethods,
  readPreferredMergeMethod,
  writePreferredMergeMethod,
} from "../lib/mergeMethod"
import { usePullRequestAction } from "../lib/usePullRequestAction"

/** GitHub's merge button: one click merges; the caret picks how. */
export function MergePullRequest({
  pr,
  apply,
  onMerged,
}: {
  pr: OpenPullRequest
  apply?: () => () => void
  onMerged?: () => void
}) {
  const [choice, setChoice] = useState<MergeMethod | null>(null)
  const [preferred] = useState(readPreferredMergeMethod)
  const [menuOpen, setMenuOpen] = useState(false)
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
    [
      choice,
      options.length === 1 ? options[0] : null,
      preferred,
      "squash" as const,
    ].find(
      (candidate): candidate is MergeMethod =>
        !!candidate && options.includes(candidate)
    ) ?? null
  const autoMerge = pr.ci === "pending" && pr.mergeState === "blocked"
  const merge = usePullRequestAction({
    pr,
    action: "merge",
    method: method ?? undefined,
    apply: autoMerge ? undefined : apply,
    onDone: () => {
      if (method) writePreferredMergeMethod(method)
      if (!autoMerge) onMerged?.()
    },
  })
  const busy = merge.isPending || merge.isSuccess || pr.autoMerge === true
  const labels = autoMerge
    ? {
        idle: "Enable auto-merge",
        pending: "Enabling auto-merge…",
        done: "Auto-merge enabled",
        retry: "Retry auto-merge",
      }
    : githubActions.merge.labels
  const state = actionLabel(labels, merge)
  return (
    <SplitButton
      disabled={!pr.headSha || busy || allowed.isPending}
      onClick={() => (method ? merge.mutate() : setMenuOpen(true))}
      menuLabel={`Merge method for PR #${pr.number}`}
      menuDisabled={busy}
      menuOpen={menuOpen}
      onMenuOpenChange={setMenuOpen}
      menu={
        options.length > 1 &&
        options.map((option) => (
          <DropdownMenuItem
            key={option}
            role="menuitemradio"
            aria-checked={option === method}
            onSelect={() => setChoice(option)}
            className="w-72 items-start"
          >
            <CheckIcon
              aria-hidden
              size={14}
              weight="bold"
              className={option === method ? "mt-0.5" : "invisible mt-0.5"}
            />
            <span>
              <span className="block font-medium">
                {mergeMethodCopy[option].label}
              </span>
              <span className="block text-secondary">
                {mergeMethodCopy[option].description}
              </span>
            </span>
          </DropdownMenuItem>
        ))
      }
    >
      {pr.autoMerge
        ? "Auto-merge enabled"
        : allowed.isPending
          ? "Merge"
          : !method
            ? "Choose how to merge"
            : !autoMerge && state === labels.idle
              ? mergeMethodCopy[method].button
              : state}
    </SplitButton>
  )
}
