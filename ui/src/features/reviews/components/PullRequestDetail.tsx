import { XIcon } from "@langchain/macaw-components/icons"
import { Text } from "@langchain/macaw-components/Text"
import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useRef, useState, type ReactNode } from "react"
import { toast } from "sonner"

import type {
  OpenPullRequest,
  PreviewCheck,
  PreviewThread,
  PullRequestPreview,
} from "@/lib/api"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { HumanInputText } from "./HumanInputCard"
import { TextPopover } from "./TextPopover"
import { PullRequestFiles } from "./PullRequestFiles"
import { api } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { cn } from "@/lib/utils"
import {
  PULL_REQUEST_STATUS,
  pullRequestPreviewQuery,
} from "@/features/reviews/lib/cache"
import { useScrollAnchor } from "@/features/reviews/lib/scrollAnchor"
import {
  useAgentBatch,
  useAgentBatchStore,
  useSubmitAgentBatch,
} from "@/features/reviews/lib/agentBatch"
import { pullRequestKey } from "@/features/reviews/lib/status"
import {
  PullRequestActions,
  type PullRequestOutcome,
} from "./PullRequestActions"

const skippedConclusions = new Set(["neutral", "skipped"])

function checkTone(check: PreviewCheck): string {
  const rank = checkRank(check)
  if (rank === 1) return "text-status-yellow"
  if (rank === 2) return "text-status-green"
  if (rank === 3) return "text-secondary"
  return "text-status-red"
}

function checkRank(check: PreviewCheck): number {
  if (check.status !== "completed") return 1
  if (check.conclusion === "success") return 2
  if (check.conclusion && skippedConclusions.has(check.conclusion)) return 3
  return 0
}

const checkMarks = ["✕", "•", "✓", "–"] as const

function Section({
  heading,
  count,
  action,
  children,
}: {
  heading: string
  count?: string
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="border-t border-default px-space-4 py-space-4 first:border-t-0">
      <div className="mb-space-2 flex items-baseline gap-space-2">
        <Text as="h3" variant="sm" weight="medium" color="primary">
          {heading}
        </Text>
        {count && (
          <span className="text-xs text-secondary tabular-nums">{count}</span>
        )}
        {action && <div className="ml-auto">{action}</div>}
      </div>
      {children}
    </section>
  )
}

function CheckRow({ check }: { check: PreviewCheck }) {
  return (
    <li className="flex items-baseline gap-space-3 py-0.5 text-xs">
      <span className={cn("shrink-0 tabular-nums", checkTone(check))}>
        {checkMarks[checkRank(check)]}
      </span>
      <span className="min-w-0 flex-1 truncate text-primary">
        {check.url ? (
          <a
            className="hover:underline"
            href={check.url}
            target="_blank"
            rel="noreferrer"
          >
            {check.name}
          </a>
        ) : (
          check.name
        )}
      </span>
      <span className={cn("shrink-0", checkTone(check))}>
        {check.status !== "completed"
          ? check.status
          : (check.conclusion ?? "done")}
      </span>
    </li>
  )
}

// A repo with hundreds of checks turns the preview into a wall of green, so
// past this many they collapse and only what needs attention stays open.
const groupChecksAbove = 20

const checkGroups = [
  ["Failing", 0],
  ["Running", 1],
  ["Passed", 2],
  ["Skipped", 3],
] as const

function Checks({ checks }: { checks: Array<PreviewCheck> | null }) {
  if (checks === null) {
    return (
      <p className="text-xs text-warning-secondary">
        GitHub did not return the checks for this commit.
      </p>
    )
  }
  if (checks.length === 0) {
    return <p className="text-xs text-secondary">No checks ran.</p>
  }
  const sorted = [...checks].sort((a, b) => checkRank(a) - checkRank(b))
  if (sorted.length <= groupChecksAbove) {
    return (
      <ul className="space-y-0.5">
        {sorted.map((check) => (
          <CheckRow key={`${check.name}:${check.url ?? ""}`} check={check} />
        ))}
      </ul>
    )
  }
  return (
    <div className="space-y-space-2">
      {checkGroups.map(([label, rank]) => {
        const group = sorted.filter((check) => checkRank(check) === rank)
        if (!group.length) return null
        return (
          <details key={label} open={rank === 0} className="group">
            <summary className="cursor-pointer list-none text-xs text-secondary hover:text-primary">
              <span aria-hidden="true" className="inline-block w-3">
                {"›"}
              </span>
              {label}
              <span className="ml-space-1 tabular-nums">{group.length}</span>
            </summary>
            <ul className="mt-space-1 space-y-0.5 pl-space-3">
              {group.map((check) => (
                <CheckRow
                  key={`${check.name}:${check.url ?? ""}`}
                  check={check}
                />
              ))}
            </ul>
          </details>
        )
      })}
    </div>
  )
}

interface PullRequestRef {
  repo: string
  number: number
}

function useResolveThreads(target: PullRequestRef) {
  const queryClient = useQueryClient()
  const previewKey = pullRequestPreviewQuery(target).queryKey
  return useMutation({
    mutationFn: (threadIds: Array<string>) =>
      api.resolveReviewThreads(target.repo, target.number, threadIds),
    meta: { errorTitle: "Couldn't resolve conversations" },
    onMutate: async (threadIds) => {
      const resolving = new Set(threadIds)
      const undo = await optimisticUpdate<PullRequestPreview>(
        queryClient,
        previewKey,
        (current) =>
          current.unresolved
            ? {
                ...current,
                unresolved: current.unresolved.filter(
                  (thread) =>
                    thread.thread_id === null ||
                    !resolving.has(thread.thread_id)
                ),
              }
            : current
      )
      return { undo }
    },
    onError: (_error, _threadIds, context) => context?.undo(),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: previewKey })
      void queryClient.invalidateQueries({ queryKey: [PULL_REQUEST_STATUS] })
      if (result.failed.length)
        toast.error(
          `Could not resolve ${result.failed.length} conversation${result.failed.length === 1 ? "" : "s"}`
        )
    },
  })
}

function AddToAgentBatch({
  target,
  commentUrl,
}: {
  target: PullRequestRef
  commentUrl: string
}) {
  const key = pullRequestKey(target)
  const entry = useAgentBatch(key).find(
    ({ item }) => item.kind === "thread" && item.commentUrl === commentUrl
  )
  const add = useAgentBatchStore((state) => state.add)
  const remove = useAgentBatchStore((state) => state.remove)
  if (entry?.state === "sent")
    return (
      <Button size="xs" color="secondary" variant="outlined" disabled>
        Sent to agent
      </Button>
    )
  if (entry)
    return (
      <Button
        size="xs"
        color="secondary"
        variant="outlined"
        title="Remove from the agent batch"
        rightDecorator={XIcon}
        onClick={() => remove(key, entry.item.id)}
      >
        Queued for agent
      </Button>
    )
  return (
    <TextPopover
      trigger={
        <Button size="xs" color="secondary" variant="outlined">
          Add to agent batch
        </Button>
      }
      title="Queue this comment for the agent"
      description="Queued comments go to the agent together when you send the batch. It addresses each on the PR branch and replies on the thread."
      placeholder="Instructions (optional)"
      submitLabel="Add to batch"
      onSubmit={async (instructions) =>
        add(key, {
          kind: "thread",
          id: crypto.randomUUID(),
          commentUrl,
          instructions,
        })
      }
    />
  )
}

function AgentBatchBar({ target }: { target: PullRequestRef }) {
  const key = pullRequestKey(target)
  const queued = useAgentBatch(key).filter(({ state }) => state === "queued")
  const discard = useAgentBatchStore((state) => state.discard)
  const submit = useSubmitAgentBatch(target)
  if (!queued.length) return null
  return (
    <div className="flex items-center gap-space-2 border-t border-default bg-surface-level-1 px-space-4 py-space-3">
      <span className="text-xs text-primary">
        {queued.length} comment{queued.length === 1 ? "" : "s"} queued for the
        agent
      </span>
      <Button
        size="xs"
        color="secondary"
        variant="outlined"
        className="ml-auto"
        onClick={() => discard(key)}
      >
        Discard
      </Button>
      <Button
        size="xs"
        onClick={() => submit.mutate(queued.map(({ item }) => item))}
      >
        Send to agent
      </Button>
    </div>
  )
}

function Conversation({
  thread,
  target,
  resolve,
}: {
  thread: PreviewThread
  target: PullRequestRef
  resolve: ReturnType<typeof useResolveThreads>
}) {
  const [open, setOpen] = useState(false)
  const [clamped, setClamped] = useState(false)
  // Only offer the toggle when there is something hidden to show.
  const measure = useCallback((node: HTMLParagraphElement | null) => {
    if (node) setClamped(node.scrollHeight > node.clientHeight + 1)
  }, [])

  return (
    <li className="border-l-2 border-warning pl-space-3">
      <div className="flex items-center gap-space-2 text-xs text-secondary">
        <span className="font-medium text-primary">
          {thread.author ?? "Someone"}
        </span>
        <span className="min-w-0 truncate font-mono">
          {thread.path}
          {thread.line !== null && `:${thread.line}`}
        </span>
        <div className="ml-auto flex shrink-0 items-center gap-space-2 text-primary">
          {thread.url && (
            <AddToAgentBatch target={target} commentUrl={thread.url} />
          )}
          {thread.thread_id && (
            <Button
              size="xs"
              color="secondary"
              variant="outlined"
              onClick={() => resolve.mutate([thread.thread_id!])}
            >
              Resolve
            </Button>
          )}
          {thread.url && (
            <Button
              size="xs"
              color="secondary"
              variant="plain"
              leftDecorator={GithubLogoIcon}
              as={<a href={thread.url} target="_blank" rel="noreferrer" />}
            >
              Reply
            </Button>
          )}
        </div>
      </div>
      {open ? (
        <div className="mt-space-1 max-w-[72ch]">
          <Markdown content={thread.body} />
          {thread.replies.length > 0 && (
            <ul className="mt-space-2 space-y-space-2 border-t border-default pt-space-2">
              {thread.replies.map((reply, index) => (
                <li key={reply.url ?? index}>
                  <span className="text-xs font-medium text-primary">
                    {reply.author ?? "Someone"}
                  </span>
                  <Markdown content={reply.body} />
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <p
          ref={measure}
          className="mt-space-1 line-clamp-3 text-xs whitespace-pre-wrap text-primary"
        >
          {thread.body}
        </p>
      )}
      {(clamped || open || thread.replies.length > 0) && (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="mt-space-1 text-xs text-secondary hover:text-primary hover:underline"
        >
          {open
            ? "Show less"
            : thread.replies.length > 0
              ? `Show more · ${thread.replies.length} ${thread.replies.length === 1 ? "reply" : "replies"}`
              : "Show more"}
        </button>
      )}
    </li>
  )
}

function Conversations({
  preview,
  target,
  resolve,
}: {
  preview: PullRequestPreview
  target: PullRequestRef
  resolve: ReturnType<typeof useResolveThreads>
}) {
  if (preview.unresolved === null) {
    return (
      <p className="text-xs text-warning-secondary">
        GitHub did not return the review threads, so unresolved comments cannot
        be counted here. Open the PR to check.
      </p>
    )
  }
  if (preview.unresolved.length === 0) {
    return (
      <p className="text-xs text-secondary">
        Nothing unresolved. Every review thread on this PR is closed.
      </p>
    )
  }
  return (
    <ul className="space-y-space-3">
      {preview.unresolved.map((thread, index) => (
        <Conversation
          key={
            thread.thread_id ??
            thread.url ??
            `${thread.path}:${thread.line}:${index}`
          }
          thread={thread}
          target={target}
          resolve={resolve}
        />
      ))}
    </ul>
  )
}

/** One pull request beside the list, in the shape of GitHub's own summary. */
export function PullRequestDetail({
  pr,
  login,
  outcome,
  onClose,
  onSettled,
  onReady,
  expandedFiles,
  scrollAnchor,
  onPositionChange,
}: {
  pr: OpenPullRequest
  login: string
  outcome?: PullRequestOutcome
  onClose: () => void
  onSettled: (outcome: PullRequestOutcome | undefined) => void
  onReady: () => void
  expandedFiles?: Array<string>
  scrollAnchor?: string
  onPositionChange: (changes: { files?: Array<string>; at?: string }) => void
}) {
  const preview = useQuery(pullRequestPreviewQuery(pr))
  const data = preview.data
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const onScrollAnchor = useCallback(
    (at: string | undefined) => onPositionChange({ at }),
    [onPositionChange]
  )
  useScrollAnchor(scrollRef, scrollAnchor, onScrollAnchor, Boolean(data))
  const resolve = useResolveThreads(pr)
  const resolvableIds = (data?.unresolved ?? []).flatMap((thread) =>
    thread.thread_id ? [thread.thread_id] : []
  )
  const failing =
    data?.checks?.filter((check) => checkRank(check) === 0).length ?? 0
  const running =
    data?.checks?.filter((check) => checkRank(check) === 1).length ?? 0

  return (
    <aside
      aria-label={`Pull request ${pr.repo} #${pr.number}`}
      className="flex min-h-0 w-full flex-col overflow-hidden rounded-lg border border-default bg-surface-level-1"
    >
      <header className="flex items-start gap-space-3 border-b border-default px-space-4 py-space-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-space-2 text-xs text-secondary">
            <span className="truncate">{pr.repo}</span>
            <span className="font-mono tabular-nums">#{pr.number}</span>
            {data && (
              <span className="tabular-nums">
                <span className="text-success-secondary">
                  +{data.additions}
                </span>{" "}
                <span className="text-error-secondary">−{data.deletions}</span>
              </span>
            )}
          </div>
          <Text
            as="h2"
            variant="h5"
            weight="medium"
            color="primary"
            className="mt-space-1 break-words"
          >
            {data?.title ?? pr.title}
          </Text>
          {data && (
            <p className="mt-space-1 text-xs text-secondary">
              {data.author ?? "Someone"} wants to merge {data.commits}{" "}
              {data.commits === 1 ? "commit" : "commits"} into{" "}
              <span className="font-mono">{data.base_ref}</span> from{" "}
              <span className="font-mono">{data.head_ref}</span>
            </p>
          )}
        </div>
        <IconButton
          icon={XIcon}
          label="Close pull request preview"
          color="secondary"
          variant="plain"
          className="-mt-space-1 -mr-space-1"
          onClick={onClose}
        />
      </header>

      <div className="border-b border-default px-space-4 py-space-3">
        <PullRequestActions
          pr={pr}
          login={login}
          outcome={outcome}
          onSettled={onSettled}
          onReady={onReady}
        />
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        {preview.isPending && (
          <div className="space-y-space-3 p-space-4">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        )}
        {preview.error && (
          <p
            role="alert"
            className="px-space-4 py-space-4 text-xs text-error-secondary"
          >
            {preview.error.message}
          </p>
        )}
        {data && (
          <>
            <Section heading="Description">
              {data.body ? (
                <div className="max-w-[72ch]">
                  <Markdown content={data.body} />
                </div>
              ) : (
                <p className="text-xs text-secondary">
                  This PR has no description.
                </p>
              )}
            </Section>

            {data.human_input && (
              <Section heading="Human input">
                <HumanInputText summary={data.human_input} />
              </Section>
            )}

            <Section
              heading="Unresolved comments"
              count={
                data.unresolved === null
                  ? undefined
                  : String(data.unresolved.length)
              }
              action={
                resolvableIds.length > 1 && (
                  <Button
                    size="xs"
                    color="secondary"
                    variant="outlined"
                    onClick={() => resolve.mutate(resolvableIds)}
                  >
                    Resolve all
                  </Button>
                )
              }
            >
              <Conversations preview={data} target={pr} resolve={resolve} />
            </Section>

            <Section
              heading="Changed files"
              count={
                data.changed_files > data.files.length
                  ? `${data.files.length} of ${data.changed_files}`
                  : String(data.changed_files)
              }
            >
              {data.files.length === 0 ? (
                <p className="text-xs text-secondary">No files changed.</p>
              ) : (
                <PullRequestFiles
                  pr={pr}
                  login={login}
                  files={data.files}
                  expanded={expandedFiles ?? []}
                  onExpandedChange={(files) =>
                    onPositionChange({
                      files: files.length ? files : undefined,
                    })
                  }
                />
              )}
            </Section>

            <Section
              heading="Checks"
              count={
                data.checks === null
                  ? undefined
                  : failing || running
                    ? `${failing} failing, ${running} running, ${data.checks.length} total`
                    : String(data.checks.length)
              }
            >
              <Checks checks={data.checks} />
            </Section>
          </>
        )}
      </div>
      <AgentBatchBar target={pr} />
    </aside>
  )
}
