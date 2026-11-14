import { CheckIcon } from "@langchain/macaw-components/icons"
import { useIsMutating, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { ConfirmDialog } from "@/components/ConfirmDialog"
import { DropdownMenuItem } from "@/components/MenuItem"
import { api, type MergeMethod, type OpenPullRequest } from "@/lib/api"
import { expiresInBrowser } from "@/lib/query"
import { SplitButton } from "@/components/SplitButton"
import { PullRequestCache } from "../lib/cache"
import { actionLabel, githubActions } from "../lib/githubActions"
import {
  mergeMethodCopy,
  mergeMethods,
  readPreferredMergeMethod,
  writePreferredMergeMethod,
} from "../lib/mergeMethod"
import { mergesLater } from "../lib/status"
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
  const queryClient = useQueryClient()
  const cache = new PullRequestCache(queryClient, pr)
  const [choice, setChoice] = useState<MergeMethod | null>(null)
  const [preferred] = useState(readPreferredMergeMethod)
  const [menuOpen, setMenuOpen] = useState(false)
  const [confirming, setConfirming] = useState(false)
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
  const autoMerge = mergesLater(pr)
  const mutationKey = ["pull-request-merge", pr.repo.toLowerCase(), pr.number]
  const pending = useIsMutating({ mutationKey })
  const [autoMergeOverride, setAutoMergeOverride] = useState<boolean | null>(
    null
  )
  const merge = usePullRequestAction({
    pr,
    action: "merge",
    mutationKey,
    method: method ?? undefined,
    apply: autoMerge ? undefined : apply,
    onDone: (queued) => {
      if (method) writePreferredMergeMethod(method)
      if (queued) {
        setAutoMergeOverride(true)
        cache.setAutoMerge(true)
      } else onMerged?.()
    },
  })
  const autoMergeEnabled = autoMergeOverride ?? pr.autoMerge === true
  const disable = usePullRequestAction({
    pr,
    action: "disable-auto-merge",
    mutationKey,
    apply: () => {
      const previous = autoMergeOverride
      setAutoMergeOverride(false)
      return () => setAutoMergeOverride(previous)
    },
    onDone: () => {
      cache.setAutoMerge(false)
      merge.reset()
    },
  })
  const [snapshot, setSnapshot] = useState({
    autoMerge: pr.autoMerge,
    headSha: pr.headSha,
  })
  if (snapshot.autoMerge !== pr.autoMerge || snapshot.headSha !== pr.headSha) {
    setSnapshot({ autoMerge: pr.autoMerge, headSha: pr.headSha })
    setAutoMergeOverride(null)
  }
  const busy = pending > 0 || (merge.isSuccess && merge.data !== true)
  const labels =
    autoMerge || (merge.data === true && autoMergeEnabled)
      ? {
          idle: "Enable auto-merge",
          pending: "Enabling auto-merge…",
          done: "Auto-merge enabled",
          retry: "Retry auto-merge",
        }
      : githubActions.merge.labels
  const state =
    merge.data === true && !autoMergeEnabled
      ? labels.idle
      : actionLabel(labels, merge)
  const rulesUnknown = pr.conversationResolutionRequired == null
  const blocking =
    pr.conversationResolutionRequired === true ||
    (rulesUnknown && pr.mergeState !== "clean")
      ? (pr.unresolvedThreads ?? 0)
      : 0
  const conversations = `${blocking} unresolved conversation${blocking === 1 ? "" : "s"}`
  const start = () => {
    if (queryClient.isMutating({ mutationKey }) > 0) return
    if (autoMergeEnabled) disable.mutate()
    else if (!method) setMenuOpen(true)
    else if (blocking > 0) setConfirming(true)
    else merge.mutate()
  }
  return (
    <>
      <SplitButton
        disabled={
          busy || (!autoMergeEnabled && (!pr.headSha || allowed.isPending))
        }
        onClick={start}
        menuLabel={`Merge method for PR #${pr.number}`}
        menuDisabled={busy || autoMergeEnabled}
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
        {autoMergeEnabled
          ? "Disable auto-merge"
          : disable.isPending
            ? "Disabling auto-merge…"
            : allowed.isPending
              ? "Merge"
              : !method
                ? "Choose how to merge"
                : !autoMerge && state === labels.idle
                  ? mergeMethodCopy[method].button
                  : state}
      </SplitButton>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={`${conversations} on PR #${pr.number}`}
        description={`${rulesUnknown ? `Could not confirm whether ${pr.repo} requires conversations to be resolved before merging.` : `${pr.repo} requires every conversation to be resolved before merging.`} Resolve ${blocking === 1 ? "it" : `all ${blocking}`} and ${autoMerge ? "enable auto-merge" : "merge"}?`}
        confirmLabel={`Resolve and ${autoMerge ? "enable auto-merge" : "merge"}`}
        onConfirm={() => {
          setConfirming(false)
          if (queryClient.isMutating({ mutationKey }) === 0)
            merge.mutate({ resolveConversations: true })
        }}
      />
    </>
  )
}
