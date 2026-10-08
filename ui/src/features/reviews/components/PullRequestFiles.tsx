import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { CaretRightIcon } from "@phosphor-icons/react/dist/ssr/CaretRight"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import { PatchDiff } from "@pierre/diffs/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"
import type {
  DiffLineAnnotation,
  FileDiffLoadedFiles,
  SelectedLineRange,
} from "@pierre/diffs"

import type {
  PreviewFile,
  PullRequestPreview,
  ReviewCommentCreate,
  ReviewDiffFile,
} from "@/lib/api"
import {
  fileContentsCacheKey,
  useDiffOptions,
  warmDiffHighlighter,
} from "@/features/agents/utils/diffUtils"
import {
  useAgentBatch,
  useAgentBatchStore,
  type BatchItem,
  type BatchItemState,
} from "@/features/reviews/lib/agentBatch"
import {
  buildCommentPayload,
  commentRangeLabel,
} from "@/features/reviews/lib/lineRange"
import { pullRequestPreviewQuery } from "@/features/reviews/lib/cache"
import { loadReviewFileContents } from "@/features/reviews/lib/fileContents"
import { pullRequestKey } from "@/features/reviews/lib/status"
import { FILE_ANCHOR_ATTRIBUTE } from "@/features/reviews/lib/scrollAnchor"
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
  added: "text-success-secondary",
  removed: "text-error-secondary",
  renamed: "text-brand-primary",
  copied: "text-brand-primary",
}

type CommentTarget = "agent" | "github"

interface Draft {
  path: string
  range: SelectedLineRange
  body: string
}

interface PostedComment {
  id: string
  path: string
  range: SelectedLineRange
  body: string
  url: string | null
}

type FileAnnotation =
  | { kind: "draft"; draft: Draft }
  | { kind: "posted"; comment: PostedComment }
  | {
      kind: "batched"
      item: Extract<BatchItem, { kind: "line" }>
      state: BatchItemState
    }

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
  const [sent, setSent] = useState<Array<PostedComment>>([])

  const restore = (comment: PostedComment) => {
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
  const optimistic = (comment: PostedComment) => {
    setDraft(null)
    setSent((current) => [...current, comment])
  }
  const payload = (comment: PostedComment): ReviewCommentCreate =>
    buildCommentPayload(comment.path, comment.range, comment.body)

  const addToBatch = useAgentBatchStore((state) => state.add)
  const removeItem = useAgentBatchStore((state) => state.remove)
  const batch = useAgentBatch(pullRequestKey(pr))
  const removeFromBatch = useCallback(
    (id: string) => removeItem(pullRequestKey(pr), id),
    [removeItem, pr]
  )

  const toGithub = useMutation({
    mutationFn: (comment: PostedComment) =>
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
    const comment = {
      id: crypto.randomUUID(),
      path: draft.path,
      range: draft.range,
      body: body.trim(),
    }
    if (target === "agent") {
      setDraft(null)
      addToBatch(pullRequestKey(pr), { kind: "line", ...comment })
    } else toGithub.mutate({ ...comment, url: null })
  }

  return { draft, setDraft, sent, batch, send, removeFromBatch }
}

/** The PR's changed files; each one expands into its full diff, open to line comments. */
export function PullRequestFiles({
  pr,
  login,
  files,
  expanded,
  onExpandedChange,
}: {
  pr: PullRequestTarget
  login: string
  files: Array<PreviewFile>
  expanded: ReadonlyArray<string>
  onExpandedChange: (paths: Array<string>) => void
}) {
  const [owner = "", name = ""] = pr.repo.split("/")
  const [wanted, setWanted] = useState(false)
  const diff = useQuery({
    queryKey: ["reviewDiff", owner, name, pr.number],
    queryFn: () => api.getReviewDiff(owner, name, pr.number),
    enabled: wanted || expanded.length > 0,
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
    onExpandedChange(
      expanded.includes(path)
        ? expanded.filter((item) => item !== path)
        : [...expanded, path]
    )

  return (
    <ul className="space-y-0.5" onPointerEnter={want} onFocus={want}>
      {files.map((file) => {
        const open = expanded.includes(file.path)
        return (
          <li key={file.path} {...{ [FILE_ANCHOR_ATTRIBUTE]: file.path }}>
            <FileRow
              file={file}
              open={open}
              onToggle={() => toggle(file.path)}
            />
            {open && (
              <div className="mt-1 mb-2 overflow-hidden rounded-md border border-default">
                {diff.isPending ? (
                  <p className="p-3 text-xs text-secondary">Loading diff…</p>
                ) : diff.error ? (
                  <p role="alert" className="p-3 text-xs text-error-secondary">
                    {diff.error.message}
                  </p>
                ) : (
                  <FileDiff
                    pr={pr}
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
      className="flex w-full items-baseline gap-2.5 rounded px-1 py-1 text-left font-mono text-xs hover:bg-surface-level-2-hover"
    >
      <CaretRightIcon
        aria-hidden="true"
        weight="regular"
        className={cn(
          "size-3 shrink-0 self-center text-icon-secondary transition-transform",
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
        <span className="text-secondary">
          {cut < 0 ? "" : file.path.slice(0, cut + 1)}
        </span>
        <span className="text-primary">{file.path.slice(cut + 1)}</span>
      </span>
      <span className="shrink-0 text-success-secondary tabular-nums">
        +{file.additions}
      </span>
      <span className="w-12 shrink-0 text-error-secondary tabular-nums">
        −{file.deletions}
      </span>
    </button>
  )
}

function FileDiff({
  pr,
  path,
  file,
  comments,
}: {
  pr: PullRequestTarget
  path: string
  file: ReviewDiffFile | undefined
  comments: ReturnType<typeof useLineComments>
}) {
  const { draft, setDraft, sent, batch, send, removeFromBatch } = comments
  const diffOptions = useDiffOptions("unified")
  const startDraft = useCallback(
    (range: SelectedLineRange) => setDraft({ path, range, body: "" }),
    [path, setDraft]
  )
  // Pierre calls this the first time context is expanded past the patch's hunks.
  const loadDiffFiles = useCallback(async (): Promise<FileDiffLoadedFiles> => {
    if (!file) throw new Error("This file is not in the loaded diff")
    const [owner = "", repo = ""] = pr.repo.split("/")
    const contents = await loadReviewFileContents(owner, repo, pr.number, file)
    const original = contents.originalContent ?? ""
    const modified = contents.modifiedContent ?? ""
    return {
      oldFile: {
        name: file.previousPath ?? file.path,
        contents: original,
        cacheKey: fileContentsCacheKey(file.path, "old", original),
      },
      newFile: {
        name: file.path,
        contents: modified,
        cacheKey: fileContentsCacheKey(file.path, "new", modified),
      },
    }
  }, [pr, file])
  const options = useMemo(
    () => ({
      ...diffOptions,
      loadDiffFiles,
      enableLineSelection: true,
      enableGutterUtility: true,
      onGutterUtilityClick: startDraft,
      onLineSelectionEnd: (range: SelectedLineRange | null) => {
        if (range) startDraft(range)
      },
    }),
    [diffOptions, loadDiffFiles, startDraft]
  )
  const fileDraft = draft?.path === path ? draft : null
  const annotations = useMemo<Array<DiffLineAnnotation<FileAnnotation>>>(
    () => [
      ...sent
        .filter((comment) => comment.path === path)
        .map((comment) => ({
          side: rangeSide(comment.range),
          lineNumber: comment.range.end,
          metadata: { kind: "posted" as const, comment },
        })),
      ...batch.flatMap(({ item, state }) =>
        item.kind === "line" && item.path === path
          ? [
              {
                side: rangeSide(item.range),
                lineNumber: item.range.end,
                metadata: { kind: "batched" as const, item, state },
              },
            ]
          : []
      ),
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
    [sent, batch, path, fileDraft]
  )
  const renderAnnotation = useCallback(
    (annotation: DiffLineAnnotation<FileAnnotation>) => {
      const meta = annotation.metadata
      if (meta.kind === "posted")
        return (
          <CommentCard
            label={
              <span className="inline-flex items-center gap-1">
                <GithubLogoIcon weight="regular" className="size-3" />
                Commented on GitHub
              </span>
            }
            href={meta.comment.url}
            range={meta.comment.range}
            body={meta.comment.body}
          />
        )
      if (meta.kind === "batched")
        return (
          <CommentCard
            label={meta.state === "sent" ? "Sent to agent" : "Queued for agent"}
            range={meta.item.range}
            body={meta.item.body}
            onRemove={
              meta.state === "queued"
                ? () => removeFromBatch(meta.item.id)
                : undefined
            }
          />
        )
      return (
        <Composer
          key={`${meta.draft.range.start}:${meta.draft.range.end}`}
          draft={meta.draft}
          onClose={() => setDraft(null)}
          onSend={send}
        />
      )
    },
    [setDraft, send, removeFromBatch]
  )
  if (!file || file.unrenderable || !file.patch) {
    return (
      <p className="p-3 text-center text-xs text-secondary">
        {file
          ? "Binary or large file — diff not shown."
          : "This file is not in the loaded diff."}
      </p>
    )
  }
  return (
    <div className="overflow-x-auto bg-surface-level-1 font-mono text-[11px] leading-5">
      <PatchDiff<FileAnnotation>
        patch={file.patch}
        // Pierre's worker pool highlights partial diffs out of step with the
        // rendered window, as on the full review page.
        disableWorkerPool
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
      <div className="overflow-hidden rounded-md border border-default bg-surface-level-1">
        <div className="flex items-center border-b border-default px-2 py-1 text-xxs">
          <span className="font-medium">
            Comment on line {commentRangeLabel(draft.range)}
          </span>
          <IconButton
            icon={XIcon}
            label="Close comment"
            size="xs"
            color="secondary"
            variant="plain"
            className="ml-auto"
            onClick={onClose}
          />
        </div>
        <div className="p-2">
          <Textarea
            ref={textareaRef}
            size="md"
            value={body}
            onChange={setBody}
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
          />
          <div className="mt-space-2 flex items-center justify-end gap-space-2">
            <Button
              size="xs"
              color="secondary"
              variant="outlined"
              leftDecorator={GithubLogoIcon}
              disabled={empty}
              onClick={() => onSend("github", body)}
            >
              Comment on GitHub
            </Button>
            <Button
              size="xs"
              disabled={empty}
              onClick={() => onSend("agent", body)}
            >
              Add to agent batch
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

function CommentCard({
  label,
  href = null,
  range,
  body,
  onRemove,
}: {
  label: ReactNode
  href?: string | null
  range: SelectedLineRange
  body: string
  onRemove?: () => void
}) {
  return (
    <div className="px-2 py-1 font-sans">
      <div className="rounded-md border border-default bg-surface-level-1 px-2.5 py-2 text-xs">
        <div className="mb-1 flex items-center gap-1.5 text-xxs text-secondary">
          {href ? (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="hover:text-primary hover:underline"
            >
              {label}
            </a>
          ) : (
            label
          )}
          <span aria-hidden="true">·</span>
          <span className="font-mono">{commentRangeLabel(range)}</span>
          {onRemove && (
            <IconButton
              icon={XIcon}
              label="Remove from agent batch"
              size="xs"
              color="secondary"
              variant="plain"
              className="ml-auto"
              onClick={onRemove}
            />
          )}
        </div>
        <p className="whitespace-pre-wrap text-primary">{body}</p>
      </div>
    </div>
  )
}
