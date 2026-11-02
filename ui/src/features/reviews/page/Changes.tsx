import { useQuery } from "@tanstack/react-query"
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { CodeView, WorkerPoolContextProvider } from "@pierre/diffs/react"
import type {
  CodeViewHandle,
  CodeViewItem,
  CodeViewReactOptions,
  DiffLineAnnotation,
  LineAnnotation,
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
import { useChatDrafts } from "@/features/reviews/lib/chatDrafts"
import { loadReviewFileContents } from "@/features/reviews/lib/fileContents"
import { toPierreSide } from "@/features/reviews/lib/lineRange"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { useAskAboutLines } from "./askAboutLines"
import { ChangesToolbar } from "./ChangesToolbar"
import {
  buildEntries,
  entryNotes,
  filterEntries,
  findEntry,
  groupSteps,
  isRenderable,
  notesSignature,
  type DiffEntry,
  type Note,
  type NoteSources,
  type StepGroup,
} from "./diffEntries"
import { EntriesContext, MarkersContext, StepsContext } from "./entries"
import { FILE_HEADER_HEIGHT, FileHeader } from "./FileHeader"
import { renderNote } from "./notes/NoteView"
import { Overview } from "./Overview"
import { reviewQueries, useFileMarkers, type PullRequestRef } from "./queries"
import { isCollapsed, useReviewPage, type DiffTarget } from "./store"
import { SelectionBar, type TextSelection } from "./SelectionBar"
import {
  isStepItem,
  ReadNudge,
  StepHeader,
  StepIntro,
  stepItemId,
  WalkthroughEnd,
} from "./StepSection"
import { FindBar } from "./FindBar"
import { FIND_HIGHLIGHT_CSS, useFindHighlights } from "./findHighlights"
import { useDiffKeys } from "./useDiffKeys"

type Item = CodeViewItem<Note>

/** Pierre's protected hook that hydrates a patch diff with full file contents. */
type LoadsFiles = { loadFilesIfNecessary(): void }

/** Expanded diff instances that already asked for full contents since mounting. */
const hydrating = new WeakSet<object>()

const NO_ITEMS: Array<Item> = []

// A diff row as painted: the line-number gutter sets the grid track, not the 18px code line.
const DIFF_ROW_HEIGHT = 20

// A step's own item is a one-line file whose file-level note is the intro; CSS hides the line.
function stepItem(group: StepGroup): Item {
  const id = stepItemId(group.step.index)
  return {
    id,
    type: "file",
    file: { name: id, contents: " ", cacheKey: id },
    annotations: [{ lineNumber: 0, metadata: { kind: "step" } }],
    version: hashText(`${group.step.title}|${group.step.summary}`),
  }
}

/** An item's version hashes everything it shows, so CodeView repaints only files that changed. */
function useCodeViewItems(
  entries: ReadonlyArray<DiffEntry>,
  steps: ReadonlyMap<number, StepGroup>,
  sources: NoteSources,
  viewed: ReadonlySet<string>,
  flipped: ReadonlySet<string>
): Array<Item> {
  return useMemo(
    () =>
      entries.flatMap((entry, i): Array<Item> => {
        const opens =
          entry.step && entries[i - 1]?.step?.index !== entry.step.index
            ? steps.get(entry.step.index)
            : undefined
        const annotations = entryNotes(entry, sources)
        const collapsed =
          !isRenderable(entry.file) ||
          isCollapsed({ viewed, collapsed: flipped }, entry)
        const hunks = entry.fileDiff.hunks
          .map(
            (hunk) =>
              `${hunk.additionStart},${hunk.additionCount},${hunk.deletionStart},${hunk.deletionCount}`
          )
          .join(";")
        const item: Item = {
          id: entry.id,
          type: "diff",
          fileDiff: entry.fileDiff,
          annotations,
          collapsed,
          version: hashText(
            `${notesSignature(annotations)}|${collapsed}|${entry.file.headSha}|${entry.file.patch?.length ?? 0}|${hunks}`
          ),
        }
        return opens ? [stepItem(opens), item] : [item]
      }),
    [entries, steps, sources, viewed, flipped]
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
  const theme = useResolvedTheme()
  const [storedOverflow] = useDiffOverflow()
  const files = diff.data?.files ?? []
  const overflow =
    files.length > 0 &&
    files.every((file) =>
      /\.(md|markdown|mdown|mkdn|mkd|mdx)$/i.test(file.path)
    )
      ? "wrap"
      : storedOverflow
  const handle = useRef<CodeViewHandle<Note>>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const paintFind = useFindHighlights(containerRef)
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
  const stepGroups = useMemo(() => groupSteps(entries), [entries])
  const stepsByIndex = useMemo(
    () => new Map(stepGroups.map((group) => [group.step.index, group])),
    [stepGroups]
  )
  const stepItems = useMemo(
    () =>
      new Map(stepGroups.map((group) => [stepItemId(group.step.index), group])),
    [stepGroups]
  )
  useEffect(() => setEntryOrder(entries), [entries, setEntryOrder])
  const draftComments = drafts?.comments
  const agentNotes = drafts?.notes
  const sources = useMemo<NoteSources>(
    () => ({
      findings: detail?.findings ?? [],
      threads: conversation?.threads ?? [],
      pending: pending.comments,
      drafts: (draftComments ?? [])
        .filter((draft) => !draft.outcome)
        .map((draft) => draft.proposal),
      agentNotes: (agentNotes ?? [])
        .filter((draft) => !draft.outcome)
        .map((draft) => draft.note),
      composer,
    }),
    [
      detail?.findings,
      conversation?.threads,
      pending.comments,
      draftComments,
      agentNotes,
      composer,
    ]
  )
  const items = useCodeViewItems(
    entries,
    stepsByIndex,
    sources,
    viewed,
    flipped
  )

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
      // Step items are the only plain files: no gutter, no surface, their placeholder line unseen.
      unsafeCSS: `${base.unsafeCSS}${FIND_HIGHLIGHT_CSS}[data-diffs-header]{border-bottom:0 !important}pre[data-file],pre[data-file] :is([data-gutter],[data-content],[data-line-annotation],[data-line]){--diffs-line-bg:transparent;background:transparent !important}pre[data-file] [data-code]{grid-template-columns:0 minmax(0,1fr)}pre[data-file] [data-gutter]{visibility:hidden;overflow:hidden}pre[data-file] [data-line]{visibility:hidden}pre[data-file] [data-annotation-content]{left:0;width:auto}[data-line-annotation],[data-gutter-buffer="annotation"]{--diffs-annotation-bg:var(--diffs-surface)}[data-gutter-buffer="annotation"]{border-right-color:transparent}[data-code],[data-diff-type=split]:is([data-overflow=wrap],[data-dehydrated]){padding-top:${DIFF_VIRTUAL_METRICS.spacing}px}${
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
        paddingTop: DIFF_VIRTUAL_METRICS.spacing,
        diffHeaderHeight: FILE_HEADER_HEIGHT,
      },
      layout: { paddingTop: 0, paddingBottom: 0, gap: 10 },
      __devOnlyValidateItemHeights: import.meta.env.DEV,
      loadDiffFiles,
      // Hunks highlighted alone lose multi-line string state; hydrate once shown.
      onPostRender: (_node, instance, phase) => {
        paintFind()
        if (phase === "unmount") return void hydrating.delete(instance)
        if (instance.options.collapsed || hydrating.has(instance)) return
        const loads = instance as unknown as Partial<LoadsFiles>
        if (typeof loads.loadFilesIfNecessary !== "function") return
        hydrating.add(instance)
        loads.loadFilesIfNecessary()
      },
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
  }, [diffStyle, overflow, theme, loadDiffFiles, commentOn, paintFind])

  const renderHeader = useCallback(
    () => (
      <>
        <Overview pr={pr} />
        <ChangesToolbar pr={pr} />
      </>
    ),
    [pr]
  )
  // Room below the last file so it can scroll to the top; the walkthrough ends right under it.
  const renderFooter = useCallback(
    () => (
      <div style={{ paddingBottom: 160 }}>
        <WalkthroughEnd groups={stepGroups} />
      </div>
    ),
    [stepGroups]
  )
  const renderCustomHeader = useCallback(
    (item: Item) =>
      isStepItem(item.id) ? (
        <StepHeader id={item.id} />
      ) : (
        <FileHeader pr={pr} id={item.id} />
      ),
    [pr]
  )
  const renderAnnotation = useCallback(
    (
      annotation: DiffLineAnnotation<Note> | LineAnnotation<Note>,
      item: Item
    ) =>
      annotation.metadata.kind === "step" ? (
        <StepIntro id={item.id} />
      ) : (
        renderNote(pr, annotation.metadata)
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
      // Arriving at a step's first file shows the step from its header.
      const opensStep =
        entry.step && stepsByIndex.get(entry.step.index)?.entries[0] === entry
      requestAnimationFrame(() => {
        if (target.kind !== "line") {
          view.scrollTo({
            type: "item",
            id:
              opensStep && entry.step ? stepItemId(entry.step.index) : entry.id,
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
    [entries, stepsByIndex]
  )

  useEffect(
    () =>
      useReviewPage.subscribe((state, previous) => {
        if (previous.composer && !state.composer)
          handle.current?.clearSelectedLines()
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
  // stays right while items above are still being measured. A step's own item
  // stands for the step's first file.
  const spyFrame = useRef(0)
  const onScroll = useCallback(
    (scrollTop: number, viewer: CoreCodeView<Note>) => {
      cancelAnimationFrame(spyFrame.current)
      spyFrame.current = requestAnimationFrame(() => {
        const container = viewer.getContainerElement()
        if (!container) return
        const atEnd =
          scrollTop + container.clientHeight >= container.scrollHeight - 2
        const edge = container.getBoundingClientRect().top + FILE_HEADER_HEIGHT
        let active: DiffEntry | null = null
        let best = -Infinity
        for (const rendered of viewer.getRenderedItems()) {
          const top = rendered.element.getBoundingClientRect().top
          if (top <= edge && top > best) {
            best = top
            active =
              entryMap.get(rendered.id) ??
              stepItems.get(rendered.id)?.entries[0] ??
              null
          }
        }
        setActive(active, atEnd)
        paintFind()
      })
    },
    [entryMap, stepItems, setActive, paintFind]
  )

  const [selection, setSelection] = useState<TextSelection | null>(null)
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
      <StepsContext.Provider value={stepItems}>
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
                // Each file is a card on the page's gutter, as on GitHub; a step
                // opens with a bare heading over its cards. The outline is a
                // shadow, so it adds nothing CodeView must measure.
                className="review-code-view h-full overflow-y-auto [&_diffs-container]:overflow-clip sm:[&_diffs-container]:mx-space-4 [&_diffs-container:has([data-step-header])]:bg-transparent [&_diffs-container:not(:has([data-step-header]))]:shadow-[0_0_0_1px_var(--border-default)] sm:[&_diffs-container:not(:has([data-step-header]))]:rounded-lg"
                renderCodeViewHeader={renderHeader}
                renderCodeViewFooter={renderFooter}
                renderCustomHeader={renderCustomHeader}
                renderAnnotation={renderAnnotation}
                onScroll={onScroll}
              />
              <ReadNudge groups={stepGroups} />
              <FindBar />
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
                    setComposer({
                      path: selection.path,
                      range: selection.range,
                    })
                    clearSelection()
                  }}
                />
              )}
            </div>
          </WorkerPoolContextProvider>
        </MarkersContext.Provider>
      </StepsContext.Provider>
    </EntriesContext.Provider>
  )
})
