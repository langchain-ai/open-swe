import { useQuery } from "@tanstack/react-query"
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
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
  hashText,
  useDiffOverflow,
} from "@/features/agents/utils/diffUtils"
import {
  readDiffSelection,
  selectedRangeFromDiff,
} from "@/features/agents/utils/diffSelection"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { useChatDrafts } from "@/features/reviews/lib/chatDrafts"
import { loadReviewFileContents } from "@/features/reviews/lib/fileContents"
import { toPierreSide } from "@/features/reviews/lib/lineRange"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { ProposedCommentCard } from "@/features/reviews/components/ProposedCommentCard"
import { PendingReviewCommentCard } from "@/features/reviews/components/PendingReviewCommentCard"
import { AgentMark } from "./AgentMark"
import { useAskAboutLines } from "./askAboutLines"
import { ChangesToolbar } from "./ChangesToolbar"
import {
  buildEntries,
  entryNotes,
  filterEntries,
  findEntry,
  isRenderable,
  notesSignature,
  type DiffEntry,
  type Note,
  type NoteSources,
} from "./diffEntries"
import { EntriesContext, MarkersContext, useEntry } from "./entries"
import { FILE_HEADER_HEIGHT, FileHeader } from "./FileHeader"
import { InlineCode } from "./inlineCode"
import { FindingNote } from "./notes/FindingNote"
import { NoteFrame } from "./notes/NoteFrame"
import { Composer } from "./notes/Composer"
import { ThreadNote } from "./notes/ThreadNote"
import { Overview } from "./Overview"
import { reviewQueries, useFileMarkers, type PullRequestRef } from "./queries"
import { isCollapsed, useReviewPage, type DiffTarget } from "./store"
import { SelectionBar, type TextSelection } from "./SelectionBar"
import { useDiffKeys } from "./useDiffKeys"

type Item = CodeViewItem<Note>

const NO_ITEMS: Array<Item> = []

// A diff row as painted: the line-number gutter sets the grid track, not the 18px code line.
const DIFF_ROW_HEIGHT = 20

/** An item's version hashes everything it shows, so CodeView repaints only files that changed. */
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
        const collapsed =
          !isRenderable(entry.file) ||
          isCollapsed({ viewed, collapsed: flipped }, entry.file.path)
        const hunks = entry.fileDiff.hunks
          .map(
            (hunk) =>
              `${hunk.additionStart},${hunk.additionCount},${hunk.deletionStart},${hunk.deletionCount}`
          )
          .join(";")
        return {
          id: entry.id,
          type: "diff",
          fileDiff: entry.fileDiff,
          annotations,
          collapsed,
          version: hashText(
            `${notesSignature(annotations)}|${collapsed}|${entry.file.headSha}|${entry.file.patch?.length ?? 0}|${hunks}`
          ),
        }
      }),
    [entries, sources, viewed, flipped]
  )
}

/** The centre column: the overview, then every file, in one virtualized scroll. */
export const Changes = memo(function Changes({ pr }: { pr: PullRequestRef }) {
  const diff = useQuery(reviewQueries.diff(pr))
  const detailQuery = useQuery(reviewQueries.detail(pr))
  const detail = detailQuery.data
  const conversation = useQuery(reviewQueries.conversation(pr)).data
  const pending = usePendingReview(pr.owner, pr.repo, pr.number)
  const drafts = useChatDrafts()
  const order = useReviewPage((state) => state.order)
  const diffStyle = useReviewPage((state) => state.diffStyle)
  const viewed = useReviewPage((state) => state.viewed)
  const flipped = useReviewPage((state) => state.collapsed)
  const composer = useReviewPage((state) => state.composer)
  const fileFilter = useReviewPage((state) => state.fileFilter)
  const setComposer = useReviewPage((state) => state.setComposer)
  const setActive = useReviewPage((state) => state.setActive)
  const setEntryOrder = useReviewPage((state) => state.setEntryOrder)
  const toggleCollapsed = useReviewPage((state) => state.toggleCollapsed)
  const theme = useResolvedTheme()
  const [overflow] = useDiffOverflow()
  const handle = useRef<CodeViewHandle<Note>>(null)
  const askAboutLines = useAskAboutLines(pr)
  const markers = useFileMarkers(pr)

  const walkthrough = detail?.walkthrough ?? null
  const allEntries = useMemo(
    () => (diff.data ? buildEntries(diff.data.files, walkthrough, order) : []),
    [diff.data, walkthrough, order]
  )
  const entries = useMemo(
    () => filterEntries(allEntries, fileFilter),
    [allEntries, fileFilter]
  )
  const entryMap = useMemo(
    () => new Map(entries.map((entry) => [entry.id, entry])),
    [entries]
  )
  useEffect(() => setEntryOrder(entries), [entries, setEntryOrder])
  const draftComments = drafts?.comments
  const sources = useMemo<NoteSources>(
    () => ({
      findings: detail?.findings ?? [],
      threads: conversation?.threads ?? [],
      pending: pending.comments,
      drafts: (draftComments ?? [])
        .filter((draft) => !draft.outcome)
        .map((draft) => draft.proposal),
      composer,
    }),
    [
      detail?.findings,
      conversation?.threads,
      pending.comments,
      draftComments,
      composer,
    ]
  )
  const items = useCodeViewItems(entries, sources, viewed, flipped)

  const filesByPath = useMemo(
    () => new Map((diff.data?.files ?? []).map((file) => [file.path, file])),
    [diff.data]
  )
  const commentOn = useCallback(
    (id: string, range: SelectedLineRange) => {
      const path = entryMap.get(id)?.file.path
      if (path) setComposer({ path, range })
    },
    [entryMap, setComposer]
  )

  const loadDiffFiles = useCallback(
    async (fileDiff: FileDiffMetadata): Promise<FileDiffLoadedFiles> => {
      const file = filesByPath.get(fileDiff.name)
      if (!file)
        throw new Error(`No file named ${fileDiff.name} in this pull request`)
      const contents = await loadReviewFileContents(
        pr.owner,
        pr.repo,
        pr.number,
        file
      )
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
    [filesByPath, pr]
  )

  const options = useMemo<CodeViewReactOptions<Note>>(() => {
    const base = buildDiffOptions(diffStyle, overflow, theme)
    return {
      ...base,
      // FileHeader draws its own rule; rows are pinned to the gutter's height so measured = laid out.
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
      onGutterUtilityClick: (range, context) =>
        commentOn(context.item.id, range),
      onLineSelectionEnd: (range, context) => {
        if (range && range.start !== range.end)
          commentOn(context.item.id, range)
      },
    }
  }, [diffStyle, overflow, theme, loadDiffFiles, commentOn])

  const renderHeader = useCallback(
    () => (
      <>
        <Overview pr={pr} />
        <ChangesToolbar pr={pr} />
      </>
    ),
    [pr]
  )
  const renderCustomHeader = useCallback(
    (item: Item) => <FileHeader pr={pr} id={item.id} />,
    [pr]
  )
  const renderAnnotation = useCallback(
    (annotation: DiffLineAnnotation<Note>, item: Item) => (
      <NoteView pr={pr} note={annotation.metadata} entryId={item.id} />
    ),
    [pr]
  )

  const highlightTimer = useRef(0)
  const scrollTo = useCallback(
    (target: DiffTarget) => {
      const view = handle.current
      if (!view) return
      if (target.kind === "top") {
        view.scrollTo({ type: "position", position: 0, behavior: "smooth" })
        return
      }
      const entry = findEntry(entries, target)
      if (!entry) return
      const path = entry.file.path
      if (
        isRenderable(entry.file) &&
        isCollapsed(useReviewPage.getState(), path)
      )
        toggleCollapsed(path)
      requestAnimationFrame(() => {
        if (target.kind !== "line") {
          view.scrollTo({
            type: "item",
            id: entry.id,
            align: "start",
            behavior: "instant",
          })
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
        view.setSelectedLines({
          id: entry.id,
          range: {
            start: Math.min(target.start ?? target.line, target.line),
            end: target.line,
            side,
          },
        })
        window.clearTimeout(highlightTimer.current)
        highlightTimer.current = window.setTimeout(() => {
          const current = view.getSelectedLines()
          if (current?.id === entry.id && current.range.end === target.line)
            view.clearSelectedLines()
        }, 2400)
      })
    },
    [entries, toggleCollapsed]
  )

  useEffect(
    () =>
      useReviewPage.subscribe((state, previous) => {
        if (state.jump && state.jump !== previous.jump)
          scrollTo(state.jump.target)
      }),
    [scrollTo]
  )

  // Switching reading order keeps you on the file you were reading.
  const shownOrder = useRef(order)
  useEffect(() => {
    if (shownOrder.current === order) return
    shownOrder.current = order
    const path = useReviewPage.getState().activePath
    if (path) scrollTo({ kind: "file", path })
  }, [order, scrollTo])

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
  // Notes size themselves to the visible pane, not to the widest code line.
  useEffect(() => {
    const node = containerRef.current
    if (!node) return
    const observer = new ResizeObserver(([entry]) => {
      if (entry)
        node.style.setProperty(
          "--review-pane-width",
          `${Math.round(entry.contentRect.width)}px`
        )
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  const onMouseUp = useCallback(
    (event: React.MouseEvent) => {
      const host = event.nativeEvent
        .composedPath()
        .find(
          (node): node is HTMLElement =>
            node instanceof HTMLElement && node.tagName === "DIFFS-CONTAINER"
        )
      const instance = handle.current?.getInstance()
      if (!host || !instance) return
      const rendered = instance
        .getRenderedItems()
        .find((item) => item.element === host)
      const path = rendered && entryMap.get(rendered.id)?.file.path
      const range = selectedRangeFromDiff(host)
      if (!path || !range) return
      setSelection({ path, range, x: event.clientX, y: event.clientY, host })
    },
    [entryMap]
  )
  const clearSelection = useCallback(() => {
    setSelection((current) => {
      if (current) readDiffSelection(current.host)?.removeAllRanges()
      return null
    })
  }, [])
  const askAboutSelection = useCallback(() => {
    if (selection) void askAboutLines(selection.path, selection.range)
    clearSelection()
  }, [selection, askAboutLines, clearSelection])

  useDiffKeys(askAboutSelection)

  return (
    <EntriesContext.Provider value={entryMap}>
      <MarkersContext.Provider value={markers}>
        <WorkerPoolContextProvider
          poolOptions={DIFF_WORKER_POOL_OPTIONS}
          highlighterOptions={DIFF_WORKER_HIGHLIGHTER_OPTIONS}
        >
          <div
            ref={containerRef}
            className="relative h-full min-h-0"
            onMouseUp={onMouseUp}
            onPointerDown={clearSelection}
          >
            <CodeView<Note>
              ref={handle}
              // Files wait for the overview above them, so they're never pushed down once painted.
              items={detailQuery.isPending ? NO_ITEMS : items}
              options={options}
              // Each file is a card on the page's gutter, as on GitHub. The
              // outline is a shadow, so it adds nothing CodeView must measure.
              className="review-code-view h-full overflow-y-auto [&_diffs-container]:overflow-clip [&_diffs-container]:shadow-[0_0_0_1px_var(--border-default)] sm:[&_diffs-container]:mx-space-4 sm:[&_diffs-container]:rounded-lg"
              renderCodeViewHeader={renderHeader}
              renderCustomHeader={renderCustomHeader}
              renderAnnotation={renderAnnotation}
              onScroll={onScroll}
            />
            {diff.isError && (
              <p
                role="alert"
                className="absolute inset-x-0 bottom-6 mx-auto w-fit rounded-lg bg-error-subtle px-space-3 py-space-2 text-xs text-error-secondary"
              >
                Couldn&apos;t load the changes: {diff.error.message}
              </p>
            )}
            {selection && (
              <SelectionBar
                selection={selection}
                onAsk={askAboutSelection}
                onComment={() => {
                  setComposer({ path: selection.path, range: selection.range })
                  clearSelection()
                }}
              />
            )}
          </div>
        </WorkerPoolContextProvider>
      </MarkersContext.Provider>
    </EntriesContext.Provider>
  )
})

/** One note on a line; each kind reads only the data it shows. */
function NoteView({
  pr,
  note,
  entryId,
}: {
  pr: PullRequestRef
  note: Note
  entryId: string
}): ReactNode {
  switch (note.kind) {
    case "step":
      return <StepIntro entryId={entryId} />
    case "finding":
      return <FindingNoteView pr={pr} id={note.id} />
    case "thread":
      return <ThreadNoteView pr={pr} id={note.id} />
    case "pending":
      return <PendingNote pr={pr} id={note.id} />
    case "draft":
      return (
        <NoteFrame>
          <ProposedCommentCard
            owner={pr.owner}
            repo={pr.repo}
            number={pr.number}
            id={note.id}
          />
        </NoteFrame>
      )
    case "composer":
      return <ComposerNote pr={pr} />
  }
}

function FindingNoteView({ pr, id }: { pr: PullRequestRef; id: string }) {
  const finding = useQuery(reviewQueries.detail(pr)).data?.findings.find(
    (candidate) => candidate.id === id
  )
  const thread = useQuery(reviewQueries.conversation(pr)).data?.threads.find(
    (candidate) => candidate.id === finding?.github_review_comment_id
  )
  return finding ? (
    <FindingNote pr={pr} finding={finding} thread={thread ?? null} />
  ) : null
}

function ThreadNoteView({ pr, id }: { pr: PullRequestRef; id: number }) {
  const thread = useQuery(reviewQueries.conversation(pr)).data?.threads.find(
    (candidate) => candidate.id === id
  )
  return thread ? <ThreadNote pr={pr} thread={thread} /> : null
}

function PendingNote({ pr, id }: { pr: PullRequestRef; id: number }) {
  const comment = usePendingReview(pr.owner, pr.repo, pr.number).comments.find(
    (candidate) => candidate.id === id
  )
  return comment ? (
    <NoteFrame className="px-space-1 py-0">
      <PendingReviewCommentCard
        owner={pr.owner}
        repo={pr.repo}
        number={pr.number}
        comment={comment}
      />
    </NoteFrame>
  ) : null
}

function ComposerNote({ pr }: { pr: PullRequestRef }) {
  const composer = useReviewPage((state) => state.composer)
  return composer ? (
    <Composer pr={pr} path={composer.path} range={composer.range} />
  ) : null
}

function StepIntro({ entryId }: { entryId: string }) {
  const step = useEntry(entryId)?.step
  if (!step) return null
  return (
    <NoteFrame className="py-space-3">
      <p className="flex items-center gap-space-2 text-xxs font-medium text-brand-primary">
        <AgentMark className="size-3" />
        Step {step.index} of {step.count}
      </p>
      <p className="mt-0.5 text-xs leading-5 font-semibold text-primary">
        <InlineCode text={step.title} />
      </p>
      {step.summary && (
        <div className="mt-space-1 max-w-[72ch] text-secondary [&_li]:text-xxs [&_li]:leading-5 [&_p]:text-xxs [&_p]:leading-5">
          <Markdown content={step.summary} />
        </div>
      )}
    </NoteFrame>
  )
}
