import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CaretRightIcon, XIcon } from "@phosphor-icons/react"
import { MultiFileDiff } from "@pierre/diffs/react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { IoLogoGithub } from "react-icons/io5"
import { toast } from "sonner"
import type { DiffLineAnnotation, SelectedLineRange } from "@pierre/diffs"

import type {
  PreviewFile,
  PullRequestPreview,
  ReviewCommentCreate,
  ReviewDiffFile,
} from "@/lib/api"
import { Button, IconButton } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  fileContentsCacheKey,
  useDiffOptions,
  warmDiffHighlighter,
} from "@/features/agents/utils/diffUtils"
import {
  buildCommentPayload,
  commentRangeLabel,
} from "@/features/reviews/components/ReviewMainBody"
import { pullRequestPreviewQuery } from "@/features/reviews/lib/cache"
import { api } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { cn } from "@/lib/utils"

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
  added: "text-emerald-700 dark:text-emerald-400",
  removed: "text-destructive",
  renamed: "text-sky-700 dark:text-sky-400",
  copied: "text-sky-700 dark:text-sky-400",
}

type CommentTarget = "agent" | "github"

interface Draft {
  path: string
  range: SelectedLineRange
  body: string
}

interface SentComment {
  id: string
  target: CommentTarget
  path: string
  range: SelectedLineRange
  body: string
  url: string | null
}

type FileAnnotation =
  | { kind: "draft"; draft: Draft }
  | { kind: "sent"; comment: SentComment }

interface PullRequestTarget {
  repo: string
  number: number
}

function rangeSide(range: SelectedLineRange) {
  return range.endSide ?? range.side ?? "additions"
}

function useLineComments(pr: PullRequestTarget, login: string) {
  const queryClient = useQueryClient()
  const [owner = "", name = ""] = pr.repo.split("/")
  const previewKey = pullRequestPreviewQuery(pr).queryKey
  const [draft, setDraft] = useState<Draft | null>(null)
  const [sent, setSent] = useState<Array<SentComment>>([])

  const restore = (comment: SentComment) => {
    setSent((current) => current.filter((item) => item.id !== comment.id))
    setDraft(
      (current) =>
        current ?? {
          path: comment.path,
          range: comment.range,
          body: comment.body,
        }
    )
  }
  const optimistic = (comment: SentComment) => {
    setDraft(null)
    setSent((current) => [...current, comment])
  }
  const payload = (comment: SentComment): ReviewCommentCreate =>
    buildCommentPayload(comment.path, comment.range, comment.body)

  const toAgent = useMutation({
    mutationFn: (comment: SentComment) =>
      api.sendLineCommentToAgent(pr.repo, pr.number, payload(comment)),
    meta: { errorTitle: "Couldn't send comment to agent" },
    onMutate: optimistic,
    onError: (_error, comment) => restore(comment),
    onSuccess: (result) => {
      toast.success(
        result.already_running
          ? `Queued behind the running agent on ${pr.repo}#${pr.number}`
          : `Sent comment to agent for ${pr.repo}#${pr.number}`
      )
      void queryClient.invalidateQueries({ queryKey: ["pr-thread-status"] })
    },
  })

  const toGithub = useMutation({
    mutationFn: (comment: SentComment) =>
      api.postReviewComment(owner, name, pr.number, payload(comment)),
    meta: { errorTitle: "Couldn't comment on GitHub" },
    onMutate: async (comment) => {
      optimistic(comment)
      const posted = payload(comment)
      const undo = await optimisticUpdate<PullRequestPreview>(
        queryClient,
        previewKey,
        (old) =>
          old.unresolved
            ? {
                ...old,
                unresolved: [
                  ...old.unresolved,
                  {
                    thread_id: null,
                    author: login,
                    body: posted.body,
                    path: posted.path,
                    line: posted.line,
                    url: null,
                    replies: [],
                  },
                ],
              }
            : old
      )
      return { undo }
    },
    onError: (_error, comment, context) => {
      context?.undo()
      restore(comment)
    },
    onSuccess: (result, comment) => {
      setSent((current) =>
        current.map((item) =>
          item.id === comment.id ? { ...item, url: result.html_url } : item
        )
      )
      void queryClient.invalidateQueries({ queryKey: previewKey })
    },
  })

  const send = (target: CommentTarget, body: string) => {
    if (!draft || !body.trim()) return
    const comment: SentComment = {
      id: crypto.randomUUID(),
      target,
      path: draft.path,
      range: draft.range,
      body: body.trim(),
      url: null,
    }
    if (target === "agent") toAgent.mutate(comment)
    else toGithub.mutate(comment)
  }

  return { draft, setDraft, sent, send }
}

/** The PR's changed files; each one expands into its full diff, open to line comments. */
export function PullRequestFiles({
  pr,
  login,
  files,
}: {
  pr: PullRequestTarget
  login: string
  files: Array<PreviewFile>
}) {
  const [owner = "", name = ""] = pr.repo.split("/")
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())
  const [wanted, setWanted] = useState(false)
  const diff = useQuery({
    queryKey: ["reviewDiff", owner, name, pr.number],
    queryFn: () => api.getReviewDiff(owner, name, pr.number),
    enabled: wanted || expanded.size > 0,
  })
  const byPath = useMemo(
    () => new Map((diff.data?.files ?? []).map((file) => [file.path, file])),
    [diff.data]
  )
  const comments = useLineComments(pr, login)
  const want = () => {
    setWanted(true)
    void warmDiffHighlighter()
  }

  const toggle = (path: string) =>
    setExpanded((current) => {
      const next = new Set(current)
      if (!next.delete(path)) next.add(path)
      return next
    })

  return (
    <ul className="space-y-0.5" onPointerEnter={want} onFocus={want}>
      {files.map((file) => {
        const open = expanded.has(file.path)
        return (
          <li key={file.path}>
            <FileRow
              file={file}
              open={open}
              onToggle={() => toggle(file.path)}
            />
            {open && (
              <div className="mt-1 mb-2 overflow-hidden rounded-md border border-border">
                {diff.isPending ? (
                  <p className="p-3 text-xs text-muted-foreground">
                    Loading diff…
                  </p>
                ) : diff.error ? (
                  <p role="alert" className="p-3 text-xs text-destructive">
                    {diff.error.message}
                  </p>
                ) : (
                  <FileDiff
                    path={file.path}
                    file={byPath.get(file.path)}
                    comments={comments}
                  />
                )}
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}

function FileRow({
  file,
  open,
  onToggle,
}: {
  file: PreviewFile
  open: boolean
  onToggle: () => void
}) {
  const cut = file.path.lastIndexOf("/")
  return (
    <button
      type="button"
      aria-expanded={open}
      onClick={onToggle}
      className="flex w-full items-baseline gap-2.5 rounded px-1 py-1 text-left font-mono text-xs hover:bg-sidebar-row-hover"
    >
      <CaretRightIcon
        aria-hidden="true"
        className={cn(
          "size-3 shrink-0 self-center text-muted-foreground transition-transform",
          open && "rotate-90"
        )}
      />
      <span
        aria-hidden="true"
        className={cn("w-3 shrink-0", fileTones[file.status])}
        title={file.status}
      >
        {fileMarks[file.status] ?? "M"}
      </span>
      <span className="min-w-0 flex-1 truncate" title={file.path}>
        <span className="text-muted-foreground">
          {cut < 0 ? "" : file.path.slice(0, cut + 1)}
        </span>
        <span className="text-foreground">{file.path.slice(cut + 1)}</span>
      </span>
      <span className="shrink-0 text-emerald-700 tabular-nums dark:text-emerald-400">
        +{file.additions}
      </span>
      <span className="w-12 shrink-0 text-destructive tabular-nums">
        −{file.deletions}
      </span>
    </button>
  )
}

function FileDiff({
  path,
  file,
  comments,
}: {
  path: string
  file: ReviewDiffFile | undefined
  comments: ReturnType<typeof useLineComments>
}) {
  const { draft, setDraft, sent, send } = comments
  const diffOptions = useDiffOptions("unified")
  const startDraft = useCallback(
    (range: SelectedLineRange) => setDraft({ path, range, body: "" }),
    [path, setDraft]
  )
  const options = useMemo(
    () => ({
      ...diffOptions,
      enableLineSelection: true,
      enableGutterUtility: true,
      onGutterUtilityClick: startDraft,
      onLineSelectionEnd: (range: SelectedLineRange | null) => {
        if (range) startDraft(range)
      },
    }),
    [diffOptions, startDraft]
  )
  const fileDraft = draft?.path === path ? draft : null
  const annotations = useMemo<Array<DiffLineAnnotation<FileAnnotation>>>(
    () => [
      ...sent
        .filter((comment) => comment.path === path)
        .map((comment) => ({
          side: rangeSide(comment.range),
          lineNumber: comment.range.end,
          metadata: { kind: "sent" as const, comment },
        })),
      ...(fileDraft
        ? [
            {
              side: rangeSide(fileDraft.range),
              lineNumber: fileDraft.range.end,
              metadata: { kind: "draft" as const, draft: fileDraft },
            },
          ]
        : []),
    ],
    [sent, path, fileDraft]
  )
  const renderAnnotation = useCallback(
    (annotation: DiffLineAnnotation<FileAnnotation>) => {
      const meta = annotation.metadata
      if (meta.kind === "sent") return <SentCard comment={meta.comment} />
      return (
        <Composer
          key={`${meta.draft.range.start}:${meta.draft.range.end}`}
          draft={meta.draft}
          onClose={() => setDraft(null)}
          onSend={send}
        />
      )
    },
    [setDraft, send]
  )
  const oldFile = useMemo(
    () => ({
      name: path,
      contents: file?.originalContent ?? "",
      cacheKey: fileContentsCacheKey(path, "old", file?.originalContent),
    }),
    [path, file?.originalContent]
  )
  const newFile = useMemo(
    () => ({
      name: path,
      contents: file?.modifiedContent ?? "",
      cacheKey: fileContentsCacheKey(path, "new", file?.modifiedContent),
    }),
    [path, file?.modifiedContent]
  )

  if (!file || file.unrenderable) {
    return (
      <p className="p-3 text-center text-xs text-muted-foreground">
        {file
          ? "Binary or large file — diff not shown."
          : "This file is not in the loaded diff."}
      </p>
    )
  }
  return (
    <div className="overflow-x-auto bg-card font-mono text-[11px] leading-5">
      <MultiFileDiff<FileAnnotation>
        oldFile={oldFile}
        newFile={newFile}
        options={options}
        lineAnnotations={annotations}
        selectedLines={fileDraft?.range ?? null}
        renderAnnotation={renderAnnotation}
      />
    </div>
  )
}

function Composer({
  draft,
  onClose,
  onSend,
}: {
  draft: Draft
  onClose: () => void
  onSend: (target: CommentTarget, body: string) => void
}) {
  const [body, setBody] = useState(draft.body)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  useEffect(() => {
    textareaRef.current?.focus()
  }, [])
  const empty = !body.trim()
  return (
    <div className="px-2 py-1 font-sans">
      <div className="overflow-hidden rounded-md border border-border bg-card">
        <div className="flex items-center border-b border-border px-2 py-1 text-[11px]">
          <span className="font-medium">
            Comment on line {commentRangeLabel(draft.range)}
          </span>
          <IconButton
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label="Close comment"
            className="ml-auto"
            onClick={onClose}
          >
            <XIcon />
          </IconButton>
        </div>
        <div className="p-2">
          <Textarea
            ref={textareaRef}
            value={body}
            onChange={(event) => setBody(event.target.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                event.preventDefault()
                onSend("agent", body)
              } else if (event.key === "Escape") {
                event.preventDefault()
                onClose()
              }
            }}
            placeholder="Leave a comment…"
            rows={3}
            className="resize-y text-xs"
          />
          <div className="mt-2 flex items-center justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={empty}
              onClick={() => onSend("github", body)}
            >
              <IoLogoGithub className="size-3.5" />
              Comment on GitHub
            </Button>
            <Button
              size="sm"
              disabled={empty}
              onClick={() => onSend("agent", body)}
            >
              Send to agent
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

function SentCard({ comment }: { comment: SentComment }) {
  return (
    <div className="px-2 py-1 font-sans">
      <div className="rounded-md border border-border bg-card px-2.5 py-2 text-xs">
        <div className="mb-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
          {comment.target === "agent" ? (
            <span>Sent to agent</span>
          ) : comment.url ? (
            <a
              href={comment.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 hover:text-foreground hover:underline"
            >
              <IoLogoGithub className="size-3" />
              Commented on GitHub
            </a>
          ) : (
            <span className="inline-flex items-center gap-1">
              <IoLogoGithub className="size-3" />
              Commented on GitHub
            </span>
          )}
          <span aria-hidden="true">·</span>
          <span className="font-mono">{commentRangeLabel(comment.range)}</span>
        </div>
        <p className="whitespace-pre-wrap text-foreground">{comment.body}</p>
      </div>
    </div>
  )
}
