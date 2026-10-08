import { useQuery } from "@tanstack/react-query"
import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import type { ReactNode } from "react"
import { CodeView, WorkerPoolContextProvider } from "@pierre/diffs/react"
import type {
  CodeViewHandle,
  CodeViewItem,
  CodeViewReactOptions,
  DiffLineAnnotation,
  FileDiffLoadedFiles,
  FileDiffMetadata,
  SelectedLineRange,
} from "@pierre/diffs/react"
import type { CodeView as CoreCodeView } from "@pierre/diffs"

import { useResolvedTheme } from "@/lib/theme"
import {
  buildDiffOptions,
  DIFF_VIRTUAL_METRICS,
  DIFF_WORKER_HIGHLIGHTER_OPTIONS,
  DIFF_WORKER_POOL_OPTIONS,
  fileContentsCacheKey,
  useDiffOverflow,
} from "@/features/agents/utils/diffUtils"
import { readDiffSelection, selectedRangeFromDiff } from "@/features/agents/utils/diffSelection"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { useChatDrafts } from "@/features/reviews/lib/chatDrafts"
import { loadReviewFileContents } from "@/features/reviews/lib/fileContents"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { ProposedCommentCard } from "@/features/reviews/components/ProposedCommentCard"
import { PendingReviewCommentCard } from "@/features/reviews/components/PendingReviewCommentCard"
import { AgentMark } from "./AgentMark"
import { useAskAboutLines } from "./askAboutLines"
import { ChangesToolbar } from "./ChangesToolbar"
import {
  buildEntries,
  entryNotes,
  isRenderable,
  notesSignature,
  type DiffEntry,
  type Note,
  type NoteSources,
} from "./diffEntries"
import { EntriesContext, FILE_HEADER_HEIGHT, useEntry } from "./entries"
import { FileHeader } from "./FileHeader"
import { InlineCode } from "./inlineCode"
import { FindingNote } from "./notes/FindingNote"
import { Composer } from "./notes/Composer"
import { ThreadNote } from "./notes/ThreadNote"
import { Overview } from "./Overview"
import { reviewQueries, type PullRequestRef } from "./queries"
import { useReviewPage, type DiffTarget } from "./store"
import { SelectionBar, type TextSelection } from "./SelectionBar"
import { useDiffKeys } from "./useDiffKeys"

type Item = CodeViewItem<Note>

// A diff row as painted: the line-number gutter sets the grid track, not the 18px code line.
const DIFF_ROW_HEIGHT = 20

function hash(text: string): number {
  let value = 0x811c9dc5
  for (let i = 0; i < text.length; i++) {
    value ^= text.charCodeAt(i)
    value = Math.imul(value, 0x01000193)
  }
  return value >>> 0
}

/**
 * CodeView repaints an item only when its version changes, so the version is
 * a hash of everything the item shows: unchanged files never repaint.
 */
function useCodeViewItems(
  entries: ReadonlyArray<DiffEntry>,
  sources: NoteSources,
  viewed: ReadonlySet<string>,
  flipped: ReadonlySet<string>
): Array<Item> {
  return useMemo(
    () =>
      entries.map((entry) => {
        const annotations = entryNotes(entry, sources)
        const path = entry.file.path
        const collapsed = !isRenderable(entry.file) || viewed.has(path) !== flipped.has(path)
        const hunks = entry.fileDiff.hunks
          .map((hunk) => `${hunk.additionStart},${hunk.additionCount},${hunk.deletionStart},${hunk.deletionCount}`)
          .join(";")
        return {
          id: entry.id,
          type: "diff",
          fileDiff: entry.fileDiff,
          annotations,
          collapsed,
          version: hash(
            `${notesSignature(annotations)}|${collapsed}|${entry.file.headSha}|${entry.file.patch?.length ?? 0}|${hunks}`
          ),
        }
      }),
    [entries, sources, viewed, flipped]
  )
}

function toPierreSide(side: "LEFT" | "RIGHT") {
  return side === "LEFT" ? "deletions" : "additions"
}

function containsLine(diff: FileDiffMetadata, line: number, side: "LEFT" | "RIGHT"): boolean {
  return diff.hunks.some((hunk) =>
    side === "LEFT"
      ? line >= hunk.deletionStart && line < hunk.deletionStart + hunk.deletionCount
      : line >= hunk.additionStart && line < hunk.additionStart + hunk.additionCount
  )
}

/** The centre column: the overview, then every file, in one virtualized scroll. */
export const Changes = memo(function Changes({ pr }: { pr: PullRequestRef }) {
  const diff = useQuery(reviewQueries.diff(pr))
  const detail = useQuery(reviewQueries.detail(pr)).data
  const conversation = useQuery(reviewQueries.conversation(pr)).data
  const pending = usePendingReview(pr.owner, pr.repo, pr.number)
  const drafts = useChatDrafts()
  const order = useReviewPage((state) => state.order)
  const diffStyle = useReviewPage((state) => state.diffStyle)
  const viewed = useReviewPage((state) => state.viewed)
  const flipped = useReviewPage((state) => state.collapsed)
  const composer = useReviewPage((state) => state.composer)
  const setComposer = useReviewPage((state) => state.setComposer)
  const setActive = useReviewPage((state) => state.setActive)
  const toggleCollapsed = useReviewPage((state) => state.toggleCollapsed)
  const theme = useResolvedTheme()
  const [overflow] = useDiffOverflow()
  const handle = useRef<CodeViewHandle<Note>>(null)
  const askAboutLines = useAskAboutLines(pr)

  const walkthrough = detail?.walkthrough ?? null
  const entries = useMemo(
    () => (diff.data ? buildEntries(diff.data.files, walkthrough, order) : []),
    [diff.data, walkthrough, order]
  )
  const entryMap = useMemo(() => new Map(entries.map((entry) => [entry.id, entry])), [entries])
  const draftComments = drafts?.comments
  const sources = useMemo<NoteSources>(
    () => ({
      findings: detail?.findings ?? [],
      threads: conversation?.threads ?? [],
      pending: pending.comments,
      drafts: (draftComments ?? []).filter((draft) => !draft.outcome).map((draft) => draft.proposal),
      composer,
    }),
    [detail?.findings, conversation?.threads, pending.comments, draftComments, composer]
  )
  const items = useCodeViewItems(entries, sources, viewed, flipped)

  const filesByName = useMemo(
    () => new Map((diff.data?.files ?? []).map((file) => [file.path, file])),
    [diff.data]
  )
  const pathOf = useCallback((id: string) => entryMap.get(id)?.file.path ?? id, [entryMap])

  const loadDiffFiles = useCallback(
    async (fileDiff: FileDiffMetadata): Promise<FileDiffLoadedFiles> => {
      const file = filesByName.get(fileDiff.name)
      if (!file) throw new Error(`No file named ${fileDiff.name} in this pull request`)
      const contents = await loadReviewFileContents(pr.owner, pr.repo, pr.number, file)
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
    },
    [filesByName, pr]
  )

  const options = useMemo<CodeViewReactOptions<Note>>(
    () => {
      const base = buildDiffOptions(diffStyle, overflow, theme)
      return {
      ...base,
      // FileHeader draws its own rule. Rows are pinned to the height the
      // gutter paints, so what CodeView measures is what it laid out.
      unsafeCSS: `${base.unsafeCSS}[data-diffs-header]{border-bottom:0 !important}${
        overflow === "scroll"
          ? `[data-line]{height:${DIFF_ROW_HEIGHT}px !important;min-height:${DIFF_ROW_HEIGHT}px !important;max-height:${DIFF_ROW_HEIGHT}px !important;line-height:${DIFF_ROW_HEIGHT}px !important}`
          : ""
      }`,
      disableFileHeader: false,
      stickyHeaders: true,
      // Exact heights: CodeView positions every file from them, so an estimate
      // that is off by a few pixels per file lands jumps short.
      itemMetrics: {
        lineHeight: DIFF_ROW_HEIGHT,
        spacing: DIFF_VIRTUAL_METRICS.spacing,
        diffHeaderHeight: FILE_HEADER_HEIGHT,
      },
      layout: { paddingTop: 0, paddingBottom: 160, gap: 10 },
      __devOnlyValidateItemHeights: import.meta.env.DEV,
      loadDiffFiles,
      enableLineSelection: true,
      enableGutterUtility: true,
      lineHoverHighlight: "number",
      onGutterUtilityClick: (range: SelectedLineRange, context: { item: { id: string } }) =>
        setComposer({ path: pathOf(context.item.id), range }),
      onLineSelectionEnd: (range: SelectedLineRange | null, context: { item: { id: string } }) => {
        if (range && range.start !== range.end)
          setComposer({ path: pathOf(context.item.id), range })
      },
      }
    },
    [diffStyle, overflow, theme, loadDiffFiles, setComposer, pathOf]
  )

  const renderHeader = useCallback(
    () => (
      <>
        <Overview pr={pr} />
        <ChangesToolbar pr={pr} />
      </>
    ),
    [pr]
  )
  const renderCustomHeader = useCallback((item: Item) => <FileHeader pr={pr} id={item.id} />, [pr])
  const renderAnnotation = useCallback(
    (annotation: DiffLineAnnotation<Note>, item: Item) => (
      <NoteView pr={pr} note={annotation.metadata} entryId={item.id} />
    ),
    [pr]
  )

  const scrollTo = useCallback(
    (target: DiffTarget) => {
      const view = handle.current
      if (!view) return
      if (target.kind === "top") {
        view.scrollTo({ type: "position", position: 0, behavior: "smooth" })
        return
      }
      const entry =
        target.kind === "entry"
          ? entries.find((candidate) => candidate.id === target.id)
          : target.kind === "line"
            ? (entries.find(
                (candidate) =>
                  candidate.file.path === target.path &&
                  (candidate.step === null ||
                    containsLine(candidate.fileDiff, target.line, target.side))
              ) ?? entries.find((candidate) => candidate.file.path === target.path))
            : entries.find((candidate) => candidate.file.path === target.path)
      if (!entry) return
      const state = useReviewPage.getState()
      const path = entry.file.path
      if (isRenderable(entry.file) && state.viewed.has(path) !== state.collapsed.has(path))
        toggleCollapsed(path)
      requestAnimationFrame(() => {
        if (target.kind === "file" || target.kind === "entry") {
          view.scrollTo({ type: "item", id: entry.id, align: "start", behavior: "instant" })
          return
        }
        const side = toPierreSide(target.side)
        view.scrollTo({
          type: "line",
          id: entry.id,
          lineNumber: target.line,
          side,
          align: "center",
          behavior: "smooth",
        })
        view.setSelectedLines({ id: entry.id, range: { start: target.line, end: target.line, side } })
        window.setTimeout(() => {
          const current = view.getSelectedLines()
          if (current?.id === entry.id && current.range.start === target.line) view.clearSelectedLines()
        }, 1800)
      })
    },
    [entries, toggleCollapsed]
  )

  useEffect(
    () =>
      useReviewPage.subscribe((state, previous) => {
        if (state.jump && state.jump !== previous.jump) scrollTo(state.jump.target)
      }),
    [scrollTo]
  )

  // The file under the top edge, read from what is actually painted, so it
  // stays right while items above are still being measured.
  const spyFrame = useRef(0)
  const onScroll = useCallback(
    (_scrollTop: number, viewer: CoreCodeView<Note>) => {
      cancelAnimationFrame(spyFrame.current)
      spyFrame.current = requestAnimationFrame(() => {
        const container = viewer.getContainerElement()
        if (!container) return
        const edge = container.getBoundingClientRect().top + FILE_HEADER_HEIGHT
        let active: DiffEntry | null = null
        let best = -Infinity
        for (const rendered of viewer.getRenderedItems()) {
          const top = rendered.element.getBoundingClientRect().top
          if (top <= edge && top > best) {
            best = top
            active = entryMap.get(rendered.id) ?? null
          }
        }
        setActive(active ? { id: active.id, path: active.file.path } : null)
      })
    },
    [entryMap, setActive]
  )

  const [selection, setSelection] = useState<TextSelection | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const onMouseUp = useCallback(
    (event: React.MouseEvent) => {
      const host = event.nativeEvent
        .composedPath()
        .find((node): node is HTMLElement => node instanceof HTMLElement && node.tagName === "DIFFS-CONTAINER")
      const instance = handle.current?.getInstance()
      if (!host || !instance) return
      const rendered = instance.getRenderedItems().find((item) => item.element === host)
      const range = selectedRangeFromDiff(host)
      if (!rendered || !range) return
      setSelection({ path: pathOf(rendered.id), range, x: event.clientX, y: event.clientY, host })
    },
    [pathOf]
  )
  const clearSelection = useCallback(() => {
    setSelection((current) => {
      if (current) readDiffSelection(current.host)?.removeAllRanges()
      return null
    })
  }, [])

  useDiffKeys({ entries, selection, clearSelection, askAboutLines, scrollTo })

  return (
    <EntriesContext.Provider value={entryMap}>
      <WorkerPoolContextProvider
        poolOptions={DIFF_WORKER_POOL_OPTIONS}
        highlighterOptions={DIFF_WORKER_HIGHLIGHTER_OPTIONS}
      >
        <div
          ref={containerRef}
          className="relative h-full min-h-0"
          onMouseUp={onMouseUp}
          onPointerDown={() => selection && clearSelection()}
        >
          <CodeView<Note>
            ref={handle}
            items={items}
            options={options}
            className="review-code-view h-full overflow-y-auto"
            renderCodeViewHeader={renderHeader}
            renderCustomHeader={renderCustomHeader}
            renderAnnotation={renderAnnotation}
            onScroll={onScroll}
          />
          {diff.isError && (
            <p role="alert" className="absolute inset-x-0 bottom-6 mx-auto w-fit rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">
              Couldn&apos;t load the changes: {diff.error.message}
            </p>
          )}
          {selection && (
            <SelectionBar
              selection={selection}
              onAsk={() => {
                void askAboutLines(selection.path, selection.range)
                clearSelection()
              }}
              onComment={() => {
                setComposer({ path: selection.path, range: selection.range })
                clearSelection()
              }}
            />
          )}
        </div>
      </WorkerPoolContextProvider>
    </EntriesContext.Provider>
  )
})

function NoteView({
  pr,
  note,
  entryId,
}: {
  pr: PullRequestRef
  note: Note
  entryId: string
}): ReactNode {
  const detail = useQuery(reviewQueries.detail(pr)).data
  const conversation = useQuery(reviewQueries.conversation(pr)).data
  const pending = usePendingReview(pr.owner, pr.repo, pr.number)
  const composer = useReviewPage((state) => state.composer)
  switch (note.kind) {
    case "step":
      return <StepIntro entryId={entryId} />
    case "finding": {
      const finding = detail?.findings.find((candidate) => candidate.id === note.id)
      if (!finding) return null
      const thread =
        conversation?.threads.find((candidate) => candidate.id === finding.github_review_comment_id) ?? null
      return <FindingNote pr={pr} finding={finding} thread={thread} />
    }
    case "thread": {
      const thread = conversation?.threads.find((candidate) => candidate.id === note.id)
      return thread ? <ThreadNote pr={pr} thread={thread} /> : null
    }
    case "pending": {
      const comment = pending.comments.find((candidate) => candidate.id === note.id)
      return comment ? (
        <div className="max-w-[784px]">
          <PendingReviewCommentCard owner={pr.owner} repo={pr.repo} number={pr.number} comment={comment} />
        </div>
      ) : null
    }
    case "draft":
      return (
        <div className="max-w-[784px] px-3 py-1.5 font-sans">
          <ProposedCommentCard owner={pr.owner} repo={pr.repo} number={pr.number} id={note.id} />
        </div>
      )
    case "composer":
      return composer ? <Composer pr={pr} path={composer.path} range={composer.range} /> : null
  }
}

function StepIntro({ entryId }: { entryId: string }) {
  const entry = useEntry(entryId)
  const step = entry?.step
  if (!step) return null
  return (
    <div className="border-b border-border bg-primary/[0.04] px-4 py-3 font-sans">
      <p className="flex items-center gap-1.5 text-[11px] font-medium text-primary">
        <AgentMark className="size-3" />
        Step {step.index} of {step.count}
      </p>
      <p className="mt-0.5 text-[13px] leading-5 font-semibold text-foreground">
        <InlineCode text={step.title} />
      </p>
      {step.summary && (
        <div className="mt-1 max-w-[72ch] text-xs leading-5 text-muted-foreground">
          <Markdown content={step.summary} />
        </div>
      )}
    </div>
  )
}
