import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useId, useRef, useState, type ReactNode } from "react"
import { toast } from "sonner"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { RecordHeader } from "@langchain/gtm-platform-design-system/patterns/record-header"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"

import {
  AlertTriangle,
  CheckCircle,
  ChevronRight,
  File,
  GitHub,
  Loader2,
  MinusCircle,
  X,
  XCircle,
  type Glyph,
} from "@/components/glyphs"

import type {
  OpenPullRequest,
  PreviewCheck,
  PreviewThread,
  PullRequestPreview,
} from "@/lib/api"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { HumanInputText } from "./HumanInputCard"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { navLink } from "../PullRequestLinks"
import { TextPopover } from "./TextPopover"
import { PullRequestFiles } from "./PullRequestFiles"
import { api } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { cn } from "@/lib/utils"
import { pullRequestPreviewQuery } from "@/features/reviews/lib/cache"
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

type CheckTone = "risk" | "attention" | "positive" | "neutral"

const checkTones: readonly CheckTone[] = [
  "risk",
  "attention",
  "positive",
  "neutral",
]

function checkRank(check: PreviewCheck): number {
  if (check.status !== "completed") return 1
  if (check.conclusion === "success") return 2
  if (check.conclusion && skippedConclusions.has(check.conclusion)) return 3
  return 0
}

const checkMarks: readonly Glyph[] = [
  XCircle,
  Loader2,
  CheckCircle,
  MinusCircle,
]

const toneInk: Record<CheckTone, string> = {
  risk: "text-risk",
  attention: "text-attention",
  positive: "text-positive",
  neutral: "text-ink-subtle",
}

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
  const headingId = useId()
  return (
    <Stack
      render={<section aria-labelledby={headingId} />}
      gap="sm"
      className="border-t border-line px-5 py-4 first:border-t-0"
    >
      <Inline gap="sm" className="min-h-control-sm">
        <Box
          render={<h3 id={headingId} />}
          className="text-label font-medium text-ink"
        >
          {heading}
        </Box>
        {count && <Badge tier="chip">{count}</Badge>}
        {action && <Box className="ml-auto">{action}</Box>}
      </Inline>
      {children}
    </Stack>
  )
}

function CheckRow({ check }: { check: PreviewCheck }) {
  const rank = checkRank(check)
  const tone = checkTones[rank]!
  return (
    <Inline render={<li />} gap="sm" className="min-h-6 text-label">
      <Icon
        icon={checkMarks[rank]!}
        size="sm"
        className={cn(
          toneInk[tone],
          rank === 1 && "animate-spin motion-reduce:animate-none"
        )}
      />
      <span className="min-w-0 flex-1 truncate text-ink">
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
      <Badge tier="plain" tone={tone}>
        {check.status !== "completed"
          ? check.status
          : (check.conclusion ?? "done")}
      </Badge>
    </Inline>
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
      <Inline gap="xs" className="text-label text-attention">
        <Icon icon={AlertTriangle} size="sm" />
        <span>GitHub did not return the checks for this commit.</span>
      </Inline>
    )
  }
  if (checks.length === 0) {
    return <p className="text-meta text-ink-subtle">No checks ran.</p>
  }
  const sorted = [...checks].sort((a, b) => checkRank(a) - checkRank(b))
  if (sorted.length <= groupChecksAbove) {
    return (
      <Stack render={<ul />} gap="none">
        {sorted.map((check) => (
          <CheckRow key={`${check.name}:${check.url ?? ""}`} check={check} />
        ))}
      </Stack>
    )
  }
  return (
    <Stack gap="xs">
      {checkGroups.map(([label, rank]) => {
        const group = sorted.filter((check) => checkRank(check) === rank)
        if (!group.length) return null
        return (
          <details key={label} open={rank === 0} className="group/checks">
            <summary className="flex w-fit cursor-pointer list-none items-center gap-1 text-meta text-ink-subtle hover:text-ink [&::-webkit-details-marker]:hidden">
              <Icon
                icon={ChevronRight}
                size="sm"
                className="transition-transform duration-fast ease-out-quint group-open/checks:rotate-90 motion-reduce:transition-none"
              />
              {label}
              <Badge tier="chip">{group.length}</Badge>
            </summary>
            <Stack render={<ul />} gap="none" className="mt-1 pl-5">
              {group.map((check) => (
                <CheckRow
                  key={`${check.name}:${check.url ?? ""}`}
                  check={check}
                />
              ))}
            </Stack>
          </details>
        )
      })}
    </Stack>
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
      void queryClient.invalidateQueries({ queryKey: ["my-pr-details"] })
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
      <Button size="compact" variant="outline" disabled>
        Sent to agent
      </Button>
    )
  if (entry)
    return (
      <Button
        size="compact"
        variant="outline"
        title="Remove from the agent batch"
        onClick={() => remove(key, entry.item.id)}
      >
        Queued for agent
        <Icon icon={X} size="sm" />
      </Button>
    )
  return (
    <TextPopover
      trigger={
        <Button size="compact" variant="outline">
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
    <Inline gap="sm" bg="muted" className="border-t border-line px-5 py-3">
      <span className="text-label text-ink">
        {queued.length} comment{queued.length === 1 ? "" : "s"} queued for the
        agent
      </span>
      <Button
        size="compact"
        variant="outline"
        className="ml-auto"
        onClick={() => discard(key)}
      >
        Discard
      </Button>
      <Button
        size="compact"
        onClick={() => submit.mutate(queued.map(({ item }) => item))}
      >
        Send to agent
      </Button>
    </Inline>
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
    <Stack render={<li />} gap="xs" className="py-3 first:pt-0 last:pb-0">
      <Inline gap="sm" className="text-meta text-ink-subtle">
        <span className="font-medium text-ink">
          {thread.author ?? "Someone"}
        </span>
        <span className="min-w-0 truncate font-mono">
          {thread.path}
          {thread.line !== null && `:${thread.line}`}
        </span>
        <Inline gap="xs" className="ml-auto shrink-0 text-ink">
          {thread.url && (
            <AddToAgentBatch target={target} commentUrl={thread.url} />
          )}
          {thread.thread_id && (
            <Button
              size="compact"
              variant="outline"
              onClick={() => resolve.mutate([thread.thread_id!])}
            >
              Resolve
            </Button>
          )}
          {thread.url && (
            <a
              className={navLink}
              href={thread.url}
              target="_blank"
              rel="noreferrer"
            >
              <Icon icon={GitHub} size="sm" />
              Reply
            </a>
          )}
        </Inline>
      </Inline>
      {open ? (
        <Box className="max-w-reading">
          <Markdown content={thread.body} />
          {thread.replies.length > 0 && (
            <Stack
              render={<ul />}
              gap="sm"
              className="mt-2 border-t border-line pt-2"
            >
              {thread.replies.map((reply, index) => (
                <li key={reply.url ?? index}>
                  <span className="text-label font-medium text-ink">
                    {reply.author ?? "Someone"}
                  </span>
                  <Markdown content={reply.body} />
                </li>
              ))}
            </Stack>
          )}
        </Box>
      ) : (
        <p
          ref={measure}
          className="line-clamp-3 text-label whitespace-pre-wrap text-ink"
        >
          {thread.body}
        </p>
      )}
      {(clamped || open || thread.replies.length > 0) && (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="w-fit text-meta text-ink-subtle hover:text-ink hover:underline"
        >
          {open
            ? "Show less"
            : thread.replies.length > 0
              ? `Show more · ${thread.replies.length} ${thread.replies.length === 1 ? "reply" : "replies"}`
              : "Show more"}
        </button>
      )}
    </Stack>
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
      <Inline gap="xs" align="start" className="text-label text-attention">
        <Icon icon={AlertTriangle} size="sm" className="mt-0.5" />
        <span>
          GitHub did not return the review threads, so unresolved comments
          cannot be counted here. Open the PR to check.
        </span>
      </Inline>
    )
  }
  if (preview.unresolved.length === 0) {
    return (
      <p className="text-meta text-ink-subtle">
        Nothing unresolved. Every review thread on this PR is closed.
      </p>
    )
  }
  return (
    <Stack render={<ul />} gap="none" className="divide-y divide-line">
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
    </Stack>
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
    <Stack
      render={<aside aria-label={`Pull request ${pr.repo} #${pr.number}`} />}
      bg="panel"
      border="line"
      radius="panel"
      className="min-h-0 w-full overflow-hidden"
    >
      <Box className="px-5 pt-3">
        <RecordHeader
          title={
            <Box
              render={<h2 />}
              className="text-title font-semibold break-words text-ink"
            >
              {data?.title ?? pr.title}
            </Box>
          }
          actions={
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label="Close pull request preview"
              onClick={onClose}
            >
              <Icon icon={X} size="md" />
            </Button>
          }
          meta={
            <Stack gap="xs" className="text-meta text-ink-subtle">
              <Inline gap="sm" align="baseline">
                <span className="truncate">{pr.repo}</span>
                <span className="font-mono tabular-nums">#{pr.number}</span>
                {data && (
                  <Inline gap="xs" className="font-mono tabular-nums">
                    <span className="text-positive">+{data.additions}</span>
                    <span className="text-risk">−{data.deletions}</span>
                  </Inline>
                )}
              </Inline>
              {data && (
                <p>
                  {data.author ?? "Someone"} wants to merge {data.commits}{" "}
                  {data.commits === 1 ? "commit" : "commits"} into{" "}
                  <span className="font-mono text-ink-muted">
                    {data.base_ref}
                  </span>{" "}
                  from{" "}
                  <span className="font-mono text-ink-muted">
                    {data.head_ref}
                  </span>
                </p>
              )}
            </Stack>
          }
        />
      </Box>

      <Box className="border-b border-line px-5 py-3">
        <PullRequestActions
          pr={pr}
          login={login}
          outcome={outcome}
          onSettled={onSettled}
          onReady={onReady}
        />
      </Box>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        {preview.isPending && (
          <Stack gap="md" padding="lg" className="px-5">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-4 w-1/2" />
          </Stack>
        )}
        {preview.error && (
          <Box className="px-5 py-4">
            <StateNotice
              tone="RISK"
              icon={AlertTriangle}
              title="Could not load this pull request"
              description={preview.error.message}
            />
          </Box>
        )}
        {data && (
          <>
            <Section heading="Description">
              {data.body ? (
                <Box className="max-w-reading">
                  <Markdown content={data.body} />
                </Box>
              ) : (
                <p className="text-meta text-ink-subtle">
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
                    size="compact"
                    variant="outline"
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
                <EmptyState icon={File} title="No files changed." />
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
    </Stack>
  )
}
