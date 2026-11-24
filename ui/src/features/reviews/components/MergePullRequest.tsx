import { CheckIcon } from "@langchain/macaw-components/icons"
import {
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@langchain/macaw-components/DropdownMenu"
import { useIsMutating, useQuery, useQueryClient } from "@tanstack/react-query"
import { useRef, useState } from "react"

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
  const [bypassRules, setBypassRules] = useState(false)
  const [confirmation, setConfirmation] = useState<
    "label" | "unknown" | "conversations" | "bypass" | null
  >(null)
  const [checkingLabels, setCheckingLabels] = useState(false)
  const checking = useRef(false)
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
  const [mergeOverride, setMergeOverride] = useState<{
    autoMerge: boolean
    inMergeQueue: boolean
  } | null>(null)
  const merge = usePullRequestAction({
    pr,
    action: "merge",
    mutationKey,
    method: method ?? undefined,
    apply: autoMerge || bypassRules || pr.mergeQueueEnabled ? undefined : apply,
    onDone: (result) => {
      if (
        method &&
        !result.in_merge_queue &&
        (!result.auto_merge || !pr.mergeQueueEnabled)
      )
        writePreferredMergeMethod(method)
      if (result.auto_merge || result.in_merge_queue) {
        const inMergeQueue = result.in_merge_queue === true
        setMergeOverride({ autoMerge: !inMergeQueue, inMergeQueue })
        cache.setMergeState(!inMergeQueue, inMergeQueue)
      } else {
        if (bypassRules || pr.mergeQueueEnabled) apply?.()
        onMerged?.()
      }
    },
  })
  const autoMergeEnabled = mergeOverride?.autoMerge ?? pr.autoMerge === true
  const inMergeQueue = mergeOverride?.inMergeQueue ?? pr.inMergeQueue === true
  const disable = usePullRequestAction({
    pr,
    action: "disable-auto-merge",
    mutationKey,
    apply: () => {
      const previous = mergeOverride
      setMergeOverride({ autoMerge: false, inMergeQueue: false })
      return () => setMergeOverride(previous)
    },
    onDone: () => {
      cache.setMergeState(false)
      merge.reset()
    },
  })
  const [snapshot, setSnapshot] = useState({
    autoMerge: pr.autoMerge,
    inMergeQueue: pr.inMergeQueue,
    headSha: pr.headSha,
  })
  if (
    snapshot.autoMerge !== pr.autoMerge ||
    snapshot.inMergeQueue !== pr.inMergeQueue ||
    snapshot.headSha !== pr.headSha
  ) {
    setSnapshot({
      autoMerge: pr.autoMerge,
      inMergeQueue: pr.inMergeQueue,
      headSha: pr.headSha,
    })
    setMergeOverride(null)
  }
  const busy = pending > 0 || (merge.isSuccess && !merge.data?.auto_merge)
  const labels =
    autoMerge || (merge.data?.auto_merge === true && autoMergeEnabled)
      ? {
          idle: "Enable auto-merge",
          pending: "Enabling auto-merge…",
          done: "Auto-merge enabled",
          retry: "Retry auto-merge",
        }
      : githubActions.merge.labels
  const state =
    merge.data?.auto_merge === true && !autoMergeEnabled
      ? labels.idle
      : actionLabel(labels, merge)
  const rulesUnknown = pr.conversationResolutionRequired == null
  const blocking =
    pr.conversationResolutionRequired === true ||
    (rulesUnknown && pr.mergeState !== "clean")
      ? (pr.unresolvedThreads ?? 0)
      : 0
  const [owner, repo] = pr.repo.split("/")
  const conversations = `${blocking} unresolved conversation${blocking === 1 ? "" : "s"}`
  const action = bypassRules
    ? "bypass rules and merge"
    : autoMerge
      ? "enable auto-merge"
      : "merge"
  const bypassWarning =
    "Merge immediately without waiting for required checks or approvals. Conversations will not be resolved. GitHub still requires your account to have permission to bypass the repository’s rules."
  const conversationWarning = `${rulesUnknown ? `Could not confirm whether ${pr.repo} requires conversations to be resolved before merging.` : `${pr.repo} requires every conversation to be resolved before merging.`} Resolve ${blocking === 1 ? "it" : `all ${blocking}`} and ${action}?`
  const confirmationLabel = bypassRules
    ? "Bypass rules and merge"
    : blocking > 0
      ? `Resolve and ${action}`
      : autoMerge
        ? "Enable auto-merge anyway"
        : "Merge anyway"
  const start = async (bypass = false) => {
    if (
      (inMergeQueue && !bypass) ||
      checking.current ||
      queryClient.isMutating({ mutationKey }) > 0
    )
      return
    if (autoMergeEnabled && !bypass) {
      disable.mutate()
      return
    }
    if (!method) {
      setMenuOpen(true)
      return
    }
    setBypassRules(bypass)
    checking.current = true
    setCheckingLabels(true)
    let reason: "label" | "unknown" | "conversations" | null = null
    try {
      const pullRequestLabels = await queryClient.fetchQuery({
        queryKey: ["pullRequestLabels", owner, repo, pr.number],
        queryFn: () => api.getPullRequestLabels(owner!, repo!, pr.number),
        staleTime: 0,
        retry: false,
      })
      if (
        pullRequestLabels.selected.some(
          (label) => label.name.toLowerCase() === "do-not-merge"
        )
      )
        reason = "label"
    } catch {
      reason = "unknown"
    }
    checking.current = false
    setCheckingLabels(false)
    if (bypass || reason || blocking > 0)
      setConfirmation(reason ?? (bypass ? "bypass" : "conversations"))
    else if (queryClient.isMutating({ mutationKey }) === 0) merge.mutate()
  }
  return (
    <>
      <SplitButton
        disabled={
          inMergeQueue ||
          busy ||
          checkingLabels ||
          (!autoMergeEnabled && (!pr.headSha || allowed.isPending))
        }
        onClick={() => void start()}
        menuLabel={`Merge method for PR #${pr.number}`}
        menuDisabled={busy || checkingLabels}
        menuOpen={menuOpen}
        onMenuOpenChange={setMenuOpen}
        menu={
          <>
            {(pr.mergeQueueEnabled || inMergeQueue) && (
              <DropdownMenuLabel className="w-72 whitespace-normal">
                The queue sets the merge method. These choices apply to bypass
                only.
              </DropdownMenuLabel>
            )}
            {options.length > 1 &&
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
                    className={
                      option === method ? "mt-0.5" : "invisible mt-0.5"
                    }
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
              ))}
            {options.length > 1 && <DropdownMenuSeparator />}
            <DropdownMenuItem
              disabled={!method || !pr.headSha || allowed.isPending}
              onSelect={() => void start(true)}
              className="w-72 whitespace-normal"
            >
              Merge without waiting for requirements to be met (bypass rules)
            </DropdownMenuItem>
          </>
        }
      >
        {inMergeQueue
          ? "Queued"
          : checkingLabels
            ? "Checking labels…"
            : bypassRules && merge.isPending
              ? "Merging…"
              : autoMergeEnabled
                ? "Disable auto-merge"
                : disable.isPending
                  ? "Disabling auto-merge…"
                  : allowed.isPending
                    ? "Merge"
                    : !method
                      ? "Choose how to merge"
                      : !pr.mergeQueueEnabled &&
                          !autoMerge &&
                          state === labels.idle
                        ? mergeMethodCopy[method].button
                        : state}
      </SplitButton>
      <ConfirmDialog
        open={confirmation !== null}
        onOpenChange={(open) => {
          if (!open) {
            setConfirmation(null)
            setBypassRules(false)
          }
        }}
        title={
          confirmation === "label"
            ? `PR #${pr.number} is labeled do-not-merge`
            : confirmation === "unknown"
              ? `Could not check labels on PR #${pr.number}`
              : bypassRules
                ? `Bypass rules for PR #${pr.number}?`
                : `${conversations} on PR #${pr.number}`
        }
        description={
          bypassRules
            ? `${confirmation === "label" ? "This PR is labeled do-not-merge. " : confirmation === "unknown" ? "Could not check whether this PR is labeled do-not-merge. " : ""}${bypassWarning}`
            : confirmation === "conversations"
              ? conversationWarning
              : `${confirmation === "label" ? "This PR is labeled do-not-merge." : "Could not check whether this PR is labeled do-not-merge."} ${blocking > 0 ? `${conversations}. ${conversationWarning}` : `Are you sure you want to ${action} anyway?`}`
        }
        confirmLabel={confirmationLabel}
        destructive={confirmation !== "conversations"}
        onConfirm={() => {
          setConfirmation(null)
          if (queryClient.isMutating({ mutationKey }) === 0)
            merge.mutate({
              resolveConversations: !bypassRules && blocking > 0,
              bypassRules,
            })
        }}
      />
    </>
  )
}
