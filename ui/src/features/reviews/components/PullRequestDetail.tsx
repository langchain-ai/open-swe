import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { XIcon } from "@phosphor-icons/react"
import { useCallback, useState, type ReactNode } from "react"
import { IoLogoGithub } from "react-icons/io5"
import { toast } from "sonner"

import type {
  OpenPullRequest,
  PreviewCheck,
  PreviewFile,
  PreviewThread,
  PullRequestPreview,
} from "@/lib/api"
import { DiffStat } from "@/components/DiffStat"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { Skeleton } from "@/components/ui/skeleton"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { TooltipIconButton } from "@/components/ui/tooltip-icon-button"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { HumanInputText } from "./HumanInputCard"
import { Button } from "@/components/ui/button"
import { navLink } from "../PullRequestLinks"
import { TextPopover } from "./TextPopover"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import {
  checkOutcome,
  checkTones,
  CheckStatusIcon,
  type CheckOutcome,
} from "./CheckStatus"
import {
  PullRequestActions,
  type PullRequestOutcome,
} from "./PullRequestActions"

const fileMarks: Record<string, string> = {
  added: "A",
  removed: "D",
  modified: "M",
  renamed: "R",
  copied: "C",
  changed: "M",
  unchanged: "·",
}

const fileTones: Record<string, string> = {
  added: "text-success-foreground",
  removed: "text-destructive",
  renamed: "text-info-foreground",
  copied: "text-info-foreground",
}

const checkRanks: Record<CheckOutcome, number> = {
  failed: 0,
  running: 1,
  passed: 2,
  skipped: 3,
}

function checkRank(check: PreviewCheck): number {
  return checkRanks[checkOutcome(check)]
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
  return (
    <section className="border-t border-border px-5 py-4 first:border-t-0">
      <div className="mb-2.5 flex items-baseline gap-2">
        <h3 className="text-xs font-medium text-foreground">{heading}</h3>
        {count && (
          <span className="text-xs text-muted-foreground tabular-nums">
            {count}
          </span>
        )}
        {action && <div className="ml-auto">{action}</div>}
      </div>
      {children}
    </section>
  )
}

function FileRow({ file }: { file: PreviewFile }) {
  const cut = file.path.lastIndexOf("/")
  return (
    <li className="flex items-baseline gap-2.5 py-1 font-mono text-xs">
      <Tooltip>
        <TooltipTrigger
          render={
            <span
              aria-hidden="true"
              className={cn("w-3 shrink-0", fileTones[file.status])}
            />
          }
        >
          {fileMarks[file.status] ?? "M"}
        </TooltipTrigger>
        <TooltipPopup>{file.status}</TooltipPopup>
      </Tooltip>
      <span className="min-w-0 flex-1 truncate" title={file.path}>
        <span className="text-muted-foreground">
          {cut < 0 ? "" : file.path.slice(0, cut + 1)}
        </span>
        <span className="text-foreground">{file.path.slice(cut + 1)}</span>
      </span>
      <span className="flex w-24 shrink-0 justify-end">
        <DiffStat additions={file.additions} deletions={file.deletions} />
      </span>
    </li>
  )
}

function CheckRow({ check }: { check: PreviewCheck }) {
  return (
    <li className="flex items-baseline gap-2.5 py-0.5 text-xs">
      <CheckStatusIcon check={check} className="self-center" />
      <span className="min-w-0 flex-1 truncate text-foreground">
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
      <span className={cn("shrink-0", checkTones[checkOutcome(check)])}>
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
      <p className="text-xs text-warning-foreground">
        GitHub did not return the checks for this commit.
      </p>
    )
  }
  if (checks.length === 0) {
    return <p className="text-xs text-muted-foreground">No checks ran.</p>
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
    <div className="space-y-1.5">
      {checkGroups.map(([label, rank]) => {
        const group = sorted.filter((check) => checkRank(check) === rank)
        if (!group.length) return null
        return (
          <Collapsible key={label} defaultOpen={rank === 0}>
            <CollapsibleTrigger className="group cursor-pointer text-xs text-muted-foreground hover:text-foreground">
              <span
                aria-hidden="true"
                className="inline-block w-3 transition-transform group-data-panel-open:rotate-90"
              >
                {"›"}
              </span>
              {label}
              <span className="ml-1.5 tabular-nums">{group.length}</span>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <ul className="mt-1 space-y-0.5 pl-3">
                {group.map((check) => (
                  <CheckRow
                    key={`${check.name}:${check.url ?? ""}`}
                    check={check}
                  />
                ))}
              </ul>
            </CollapsibleContent>
          </Collapsible>
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
  const [owner, name] = target.repo.split("/")
  const previewKey = ["pr-preview", owner, name, target.number]
  return useMutation({
    mutationFn: (threadIds: Array<string>) =>
      api.resolveReviewThreads(target.repo, target.number, threadIds),
    meta: { errorTitle: "Couldn't resolve conversations" },
    onSuccess: (result) => {
      const resolved = new Set(result.resolved)
      queryClient.setQueryData<PullRequestPreview>(previewKey, (current) =>
        current?.unresolved
          ? {
              ...current,
              unresolved: current.unresolved.filter(
                (thread) =>
                  thread.thread_id === null || !resolved.has(thread.thread_id)
              ),
            }
          : current
      )
      void queryClient.invalidateQueries({ queryKey: previewKey })
      void queryClient.invalidateQueries({ queryKey: ["my-pr-details"] })
      if (result.failed.length)
        toast.error(
          `Could not resolve ${result.failed.length} conversation${result.failed.length === 1 ? "" : "s"}`
        )
    },
  })
}

function SendToAgent({
  target,
  commentUrl,
}: {
  target: PullRequestRef
  commentUrl: string
}) {
  const queryClient = useQueryClient()
  const send = useMutation({
    mutationFn: (instructions: string) =>
      api.addressPullRequestComment(
        target.repo,
        target.number,
        commentUrl,
        instructions
      ),
    meta: { errorTitle: "Couldn't send comment to agent" },
    onSuccess: (result) => {
      toast.success(
        result.already_running
          ? `Agent already running on ${target.repo}#${target.number}`
          : `Sent comment to agent for ${target.repo}#${target.number}`
      )
      void queryClient.invalidateQueries({ queryKey: ["pr-thread-status"] })
    },
  })
  return (
    <TextPopover
      trigger={
        <Button size="sm" variant="outline" disabled={send.isPending}>
          {send.isPending ? "Sending…" : "Send to agent"}
        </Button>
      }
      title="Send this comment to the agent"
      description="The agent addresses it on the PR branch and replies on the thread."
      placeholder="Instructions (optional)"
      submitLabel="Send"
      pending={send.isPending}
      onSubmit={(instructions, done) =>
        send.mutate(instructions, { onSuccess: done })
      }
    />
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
  const resolving =
    resolve.isPending &&
    thread.thread_id !== null &&
    resolve.variables.includes(thread.thread_id)
  // Only offer the toggle when there is something hidden to show.
  const measure = useCallback((node: HTMLParagraphElement | null) => {
    if (node) setClamped(node.scrollHeight > node.clientHeight + 1)
  }, [])

  return (
    <li className="border-l-2 border-warning/40 pl-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">
          {thread.author ?? "Someone"}
        </span>
        <span className="min-w-0 truncate font-mono">
          {thread.path}
          {thread.line !== null && `:${thread.line}`}
        </span>
        <div className="ml-auto flex shrink-0 items-center gap-2 text-foreground">
          {thread.url && (
            <SendToAgent target={target} commentUrl={thread.url} />
          )}
          {thread.thread_id && (
            <Button
              size="sm"
              variant="outline"
              disabled={resolve.isPending}
              onClick={() => resolve.mutate([thread.thread_id!])}
            >
              {resolving ? "Resolving…" : "Resolve"}
            </Button>
          )}
          {thread.url && (
            <a
              className={navLink}
              href={thread.url}
              target="_blank"
              rel="noreferrer"
            >
              <IoLogoGithub className="size-3.5" />
              Reply
            </a>
          )}
        </div>
      </div>
      {open ? (
        <div className="mt-1 max-w-[72ch]">
          <Markdown content={thread.body} />
        </div>
      ) : (
        <p
          ref={measure}
          className="mt-1 line-clamp-3 text-xs whitespace-pre-wrap text-foreground"
        >
          {thread.body}
        </p>
      )}
      {(clamped || open) && (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="mt-1 text-xs text-muted-foreground hover:text-foreground hover:underline"
        >
          {open ? "Show less" : "Show more"}
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
      <p className="text-xs text-warning-foreground">
        GitHub did not return the review threads, so unresolved comments cannot
        be counted here. Open the PR to check.
      </p>
    )
  }
  if (preview.unresolved.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        Nothing unresolved. Every review thread on this PR is closed.
      </p>
    )
  }
  return (
    <ul className="space-y-2.5">
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
}: {
  pr: OpenPullRequest
  login: string
  outcome?: PullRequestOutcome
  onClose: () => void
  onSettled: (outcome: PullRequestOutcome) => void
  onReady: () => void
}) {
  const [owner, name] = pr.repo.split("/")
  const preview = useQuery({
    queryKey: ["pr-preview", owner, name, pr.number],
    queryFn: () => api.getPullRequestPreview(owner!, name!, pr.number),
    staleTime: 60_000,
  })
  const data = preview.data
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
      className="flex min-h-0 w-full flex-col overflow-hidden rounded-lg border border-border bg-card"
    >
      <header className="flex items-start gap-3 border-b border-border px-5 py-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2 text-xs text-muted-foreground">
            <span className="truncate">{pr.repo}</span>
            <span className="font-mono tabular-nums">#{pr.number}</span>
            {data && (
              <DiffStat additions={data.additions} deletions={data.deletions} />
            )}
          </div>
          <h2 className="mt-1 text-sm font-medium break-words text-foreground">
            {data?.title ?? pr.title}
          </h2>
          {data && (
            <p className="mt-1 text-xs text-muted-foreground">
              {data.author ?? "Someone"} wants to merge {data.commits}{" "}
              {data.commits === 1 ? "commit" : "commits"} into{" "}
              <span className="font-mono">{data.base_ref}</span> from{" "}
              <span className="font-mono">{data.head_ref}</span>
            </p>
          )}
        </div>
        <TooltipIconButton
          label="Close pull request preview"
          onClick={onClose}
          size="icon"
          className="-mt-1 -mr-1.5"
        >
          <XIcon className="size-4" />
        </TooltipIconButton>
      </header>

      <div className="border-b border-border px-5 py-3">
        <PullRequestActions
          pr={pr}
          login={login}
          outcome={outcome}
          onSettled={onSettled}
          onReady={onReady}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {preview.isPending && (
          <div className="space-y-3 p-5">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        )}
        {preview.error && (
          <p role="alert" className="px-5 py-4 text-xs text-destructive">
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
                <p className="text-xs text-muted-foreground">
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
                    size="sm"
                    variant="outline"
                    disabled={resolve.isPending}
                    onClick={() => resolve.mutate(resolvableIds)}
                  >
                    {resolve.isPending &&
                    resolve.variables.length === resolvableIds.length
                      ? "Resolving…"
                      : "Resolve all"}
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
                <p className="text-xs text-muted-foreground">
                  No files changed.
                </p>
              ) : (
                <ul>
                  {data.files.map((file) => (
                    <FileRow key={file.path} file={file} />
                  ))}
                </ul>
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
    </aside>
  )
}
