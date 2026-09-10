import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  MultiFileDiff,
  Virtualizer,
  WorkerPoolContextProvider,
  useVirtualizer,
} from "@pierre/diffs/react"
import {
  FileTree,
  useFileTree,
  useFileTreeSelection,
} from "@pierre/trees/react"
import { CaretDownIcon } from "@phosphor-icons/react"
import type { FileContents } from "@pierre/diffs/react"
import type { SelectionSide } from "@pierre/diffs"
import type { GitStatus, GitStatusEntry } from "@pierre/trees"

import type { ThreadPrDiffFile } from "@/features/agents/lib/api"
import type { ShowInDiffTarget } from "@/features/agents/lib/showInDiff"
import { DiffWrapToggle } from "@/features/agents/components/DiffWrapToggle"
import {
  DIFF_VIRTUALIZER_CONFIG,
  DIFF_VIRTUAL_METRICS,
  DIFF_WORKER_HIGHLIGHTER_OPTIONS,
  DIFF_WORKER_POOL_OPTIONS,
  fileContentsCacheKey,
  useDiffOptions,
} from "@/features/agents/utils/diffUtils"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

export interface PanelFile {
  filePath: string
  treePath: string
  additions: number
  deletions: number
  originalContent: string
  modifiedContent: string
  status: GitStatus
  patch?: string | null
  unrenderable?: boolean
}

// How long to poll for a reveal target: the file may still be windowed out of
// the virtualizer, and its diff renders a frame or two after that.
const REVEAL_MAX_FRAMES = 120
// The virtualizer offset is authoritative, but the diff can settle a little more
// after the smooth scroll lands, so it is re-read once the animation is done.
const REVEAL_SETTLE_MS = 500
interface PositionedDiff {
  getLinePosition: (
    lineNumber: number,
    side?: SelectionSide
  ) => { top: number; height: number } | undefined
}

// A file's rendered diff, captured on paint so a reveal can ask it where a line
// sits. The instance only exposes getLinePosition once it has laid out.
interface RegisteredDiff {
  host: HTMLElement
  instance: unknown
}

function hasLinePosition(instance: unknown): instance is PositionedDiff {
  return (
    typeof (instance as { getLinePosition?: unknown } | null)
      ?.getLinePosition === "function"
  )
}

type DiffVirtualizer = NonNullable<ReturnType<typeof useVirtualizer>>

// Absolute scrollTop that centers a diff line, or null while the diff has no
// position for it yet. The file's own offset comes from the virtualizer rather
// than from its client rect: while rows above are still swapping estimated
// heights for measured ones the rect lies, and the target lands short.
function diffLineCenterTarget(
  registered: RegisteredDiff,
  lineNumber: number,
  side: SelectionSide,
  scroller: HTMLElement,
  virtualizer: DiffVirtualizer | null
): number | null {
  if (!hasLinePosition(registered.instance)) return null
  const line = registered.instance.getLinePosition(lineNumber, side)
  if (!line) return null
  const hostTop = virtualizer
    ? virtualizer.getOffsetInScrollContainer(registered.host)
    : registered.host.getBoundingClientRect().top -
      scroller.getBoundingClientRect().top +
      scroller.scrollTop
  const top = hostTop + line.top - (scroller.clientHeight - line.height) / 2
  return Math.max(
    0,
    Math.min(top, scroller.scrollHeight - scroller.clientHeight)
  )
}

// The virtualizer instance is only reachable from inside <Virtualizer>, and the
// component forwards no ref; this lifts it to the parent and doubles as the
// hidden probe that finds the scroll element.
function VirtualizerBridge({
  probeRef,
  instanceRef,
}: {
  probeRef: (node: HTMLDivElement | null) => void
  instanceRef: React.MutableRefObject<DiffVirtualizer | null>
}) {
  const virtualizer = useVirtualizer()
  useEffect(() => {
    instanceRef.current = virtualizer ?? null
  }, [virtualizer, instanceRef])
  return <div ref={probeRef} aria-hidden className="hidden" />
}

function findScroller(node: HTMLElement | null): HTMLElement | null {
  for (let el = node?.parentElement ?? null; el; el = el.parentElement) {
    const overflowY = getComputedStyle(el).overflowY
    if (overflowY === "auto" || overflowY === "scroll") return el
  }
  return null
}

function prFileStatus(file: ThreadPrDiffFile): GitStatus {
  if (file.status === "added") return "added"
  if (file.status === "removed") return "deleted"
  return "modified"
}

export function commonDirPrefix(paths: Array<string>): string {
  const first = paths[0]
  if (paths.length === 0 || first === undefined) return ""
  const base = first.split("/").slice(0, -1)
  let depth = base.length
  for (const path of paths) {
    const segments = path.split("/").slice(0, -1)
    let i = 0
    while (i < depth && i < segments.length && segments[i] === base[i]) i++
    depth = i
  }
  return depth === 0 ? "" : `${base.slice(0, depth).join("/")}/`
}

export function toPanelFiles(
  diffFiles: Array<ThreadPrDiffFile>
): Array<PanelFile> {
  const prefix = commonDirPrefix(diffFiles.map((file) => file.path))
  return diffFiles.map((file) => ({
    filePath: file.path,
    treePath:
      prefix && file.path.startsWith(prefix)
        ? file.path.slice(prefix.length)
        : file.path,
    additions: file.additions,
    deletions: file.deletions,
    originalContent: file.originalContent ?? "",
    modifiedContent: file.modifiedContent ?? "",
    status: prFileStatus(file),
    patch: file.patch,
    unrenderable: file.unrenderable,
  }))
}

// Neutral filename foreground from the pierre Shiki themes (pierre-light /
// pierre-dark sidebar foreground). The tree tints filename text by git status,
// so feeding this keeps names neutral grey/white instead of accent-blue.
const TREE_FILE_FG = "light-dark(#525252, #a3a3a3)"

// Selected rows must read as high-contrast (white in dark, near-black in light)
// while the rest stay neutral. The built-in git-status content color outranks
// the selection color by specificity, so override it from the `unsafe` layer.
export const TREE_UNSAFE_CSS = `
  [data-item-selected="true"] [data-item-section="content"] {
    color: var(--trees-selected-fg);
  }

  /* On click a row is focus-ringed a frame before it's marked selected, which
   * flashes the accent outline. Pointer focus doesn't match :focus-visible, so
   * drop the ring there; keyboard navigation keeps it. */
  [data-item-focused="true"]:not(:focus-visible)::before {
    outline-color: transparent;
  }
`

export function treeThemeStyle(): React.CSSProperties {
  return {
    "--trees-theme-sidebar-bg": "var(--card)",
    "--trees-theme-sidebar-fg": "var(--foreground)",
    "--trees-theme-sidebar-border": "var(--border)",
    "--trees-theme-sidebar-header-fg": "var(--muted-foreground)",
    "--trees-theme-list-hover-bg":
      "color-mix(in oklab, var(--primary) 10%, transparent)",
    "--trees-theme-list-active-selection-bg":
      "color-mix(in oklab, var(--primary) 22%, transparent)",
    "--trees-theme-list-active-selection-fg": "var(--foreground)",
    "--trees-selected-focused-border-color-override": "transparent",
    "--trees-theme-input-bg": "var(--card)",
    "--trees-theme-input-fg": "var(--foreground)",
    "--trees-theme-input-border": "var(--border)",
    "--trees-theme-focus-ring": "var(--primary)",
    "--trees-theme-scrollbar-thumb": "var(--border)",
    "--trees-theme-git-added-fg": TREE_FILE_FG,
    "--trees-theme-git-modified-fg": TREE_FILE_FG,
    "--trees-theme-git-deleted-fg": TREE_FILE_FG,
    "--trees-theme-git-renamed-fg": TREE_FILE_FG,
    "--trees-theme-git-untracked-fg": TREE_FILE_FG,
    "--trees-theme-git-ignored-fg": "var(--muted-foreground)",
  } as React.CSSProperties
}

interface DiffFilesViewProps {
  files: Array<PanelFile>
  /**
   * File (and optionally line) to select and scroll to, set when a transcript
   * row is clicked or the agent calls `show_in_diff`. A new object re-reveals,
   * so pointing twice at the same place still moves the view.
   */
  revealTarget?: ShowInDiffTarget | null
  /** Full-screen panels have room for the file tree alongside the diff. */
  fullScreen: boolean
  emptyLabel: string
  /** The change set was capped, so `files` is not everything that changed. */
  truncated?: boolean
  hideHeader?: boolean
  /** Rendered at the start of the header row (the panel's own tabs). */
  leading?: React.ReactNode
  /** Rendered in the header row, before the wrap toggle and diff stats. */
  actions?: React.ReactNode
}

/**
 * The changed-files reader shared by cloud threads and local desktop sessions:
 * a header row with the diff totals, the virtualized per-file diffs, and the
 * file tree when there is room for it.
 */
export function DiffFilesView({
  files,
  revealTarget,
  fullScreen,
  emptyLabel,
  truncated,
  hideHeader,
  leading,
  actions,
}: DiffFilesViewProps) {
  const isMobile = useIsMobile()
  const [selectedTreePath, setSelectedTreePath] = useState<string | null>(null)
  const sectionRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const diffRefs = useRef<Record<string, RegisteredDiff>>({})
  const scrollerRef = useRef<HTMLElement | null>(null)
  const virtualizerRef = useRef<DiffVirtualizer | null>(null)

  // The Virtualizer doesn't forward a ref, so a hidden probe inside it finds the
  // scroll element that line reveals have to align against.
  const scrollerProbe = useCallback((node: HTMLDivElement | null) => {
    scrollerRef.current = findScroller(node)
  }, [])

  const registerDiff = useCallback(
    (filePath: string, registered: RegisteredDiff | null) => {
      if (registered) diffRefs.current[filePath] = registered
      else delete diffRefs.current[filePath]
    },
    []
  )

  const totals = useMemo(
    () =>
      files.reduce(
        (acc, file) => ({
          additions: acc.additions + file.additions,
          deletions: acc.deletions + file.deletions,
        }),
        { additions: 0, deletions: 0 }
      ),
    [files]
  )

  const filesRef = useRef(files)
  useEffect(() => {
    filesRef.current = files
  }, [files])
  const revealFile = useCallback((file: PanelFile) => {
    setSelectedTreePath(file.treePath)
    sectionRefs.current[file.filePath]?.scrollIntoView({
      block: "start",
      behavior: "smooth",
    })
  }, [])

  const selectTreePath = useCallback(
    (path: string) => {
      setSelectedTreePath(path)
      const target = filesRef.current.find((file) => file.treePath === path)
      if (target) revealFile(target)
    },
    [revealFile]
  )

  useEffect(() => {
    if (!revealTarget) return
    const { path, line } = revealTarget
    const side: SelectionSide =
      revealTarget.side === "old" ? "deletions" : "additions"
    let cancelled = false
    let revealed = false
    let frames = 0
    let settleTimer: number | undefined
    // The file may still be windowed out and its diff unrendered, so poll: bring
    // the card into view as soon as it exists, then scroll to the line as soon
    // as the diff can place it.
    const step = () => {
      if (cancelled) return
      // Transcript rows carry absolute paths; diff files are repo-relative.
      const file = filesRef.current.find(
        (candidate) =>
          candidate.filePath === path || path.endsWith(`/${candidate.filePath}`)
      )
      if (file) {
        if (!revealed) {
          revealed = true
          revealFile(file)
        }
        if (line === null) return
        const scroller = scrollerRef.current
        const registered = diffRefs.current[file.filePath]
        if (scroller && registered) {
          const top = diffLineCenterTarget(
            registered,
            line,
            side,
            scroller,
            virtualizerRef.current
          )
          if (top !== null) {
            scroller.scrollTo({ top, behavior: "smooth" })
            settleTimer = window.setTimeout(() => {
              if (cancelled) return
              const settled = diffLineCenterTarget(
                registered,
                line,
                side,
                scroller,
                virtualizerRef.current
              )
              if (
                settled !== null &&
                Math.abs(settled - scroller.scrollTop) > 2
              )
                scroller.scrollTo({ top: settled, behavior: "auto" })
            }, REVEAL_SETTLE_MS)
            return
          }
        }
      }
      if (frames++ < REVEAL_MAX_FRAMES) requestAnimationFrame(step)
    }
    requestAnimationFrame(step)
    return () => {
      cancelled = true
      if (settleTimer !== undefined) window.clearTimeout(settleTimer)
    }
  }, [revealTarget, revealFile])

  return (
    <>
      {!hideHeader && (
        <div className="@container flex min-h-9 flex-nowrap items-center gap-1 overflow-hidden border-b border-border px-3 py-1">
          <div className="min-w-0 flex-1">{leading}</div>
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <DiffWrapToggle />
            {actions}
            {files.length > 0 && (
              <span className="flex shrink-0 items-center gap-2 text-[11px] whitespace-nowrap text-muted-foreground/70">
                <span
                  className="@max-[620px]:hidden"
                  title={
                    truncated ? "Only the first files are shown" : undefined
                  }
                >
                  {truncated ? "first " : ""}
                  {files.length} file{files.length === 1 ? "" : "s"}
                </span>
                <span className="text-success-foreground">
                  +{totals.additions}
                </span>
                <span className="text-destructive">-{totals.deletions}</span>
              </span>
            )}
          </div>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {files.length > 0 ? (
          <WorkerPoolContextProvider
            poolOptions={DIFF_WORKER_POOL_OPTIONS}
            highlighterOptions={DIFF_WORKER_HIGHLIGHTER_OPTIONS}
          >
            <Virtualizer
              className="min-h-0 flex-1 overflow-y-auto"
              contentClassName="p-0"
              config={DIFF_VIRTUALIZER_CONFIG}
            >
              <VirtualizerBridge
                probeRef={scrollerProbe}
                instanceRef={virtualizerRef}
              />
              {files.map((file) => (
                <FileDiffSection
                  key={file.filePath}
                  file={file}
                  registerDiff={registerDiff}
                  sectionRef={(node) => {
                    sectionRefs.current[file.filePath] = node
                  }}
                />
              ))}
            </Virtualizer>
          </WorkerPoolContextProvider>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto p-6 text-center text-xs text-muted-foreground/70">
            {emptyLabel}
          </div>
        )}

        {fullScreen && !isMobile && files.length > 0 && (
          <div className="w-72 shrink-0 border-l border-border bg-card">
            <FileTreeExplorer
              files={files}
              selectedTreePath={selectedTreePath}
              onSelect={selectTreePath}
            />
          </div>
        )}
      </div>
    </>
  )
}

const FileDiffSection = memo(
  function FileDiffSection({
    file,
    registerDiff,
    sectionRef,
  }: {
    file: PanelFile
    registerDiff: (filePath: string, registered: RegisteredDiff | null) => void
    sectionRef: (node: HTMLDivElement | null) => void
  }) {
    const [open, setOpen] = useState(true)
    const baseDiffOptions = useDiffOptions()
    const diffOptions = useMemo(
      () => ({
        ...baseDiffOptions,
        onPostRender: (node: HTMLElement, instance: unknown) =>
          registerDiff(file.filePath, { host: node, instance }),
      }),
      [baseDiffOptions, file.filePath, registerDiff]
    )
    useEffect(
      () => () => registerDiff(file.filePath, null),
      [file.filePath, registerDiff]
    )
    const oldFile = useMemo<FileContents>(
      () => ({
        name: file.treePath,
        contents: file.originalContent,
        cacheKey: fileContentsCacheKey(
          file.filePath,
          "old",
          file.originalContent
        ),
      }),
      [file.filePath, file.originalContent, file.treePath]
    )
    const newFile = useMemo<FileContents>(
      () => ({
        name: file.treePath,
        contents: file.modifiedContent,
        cacheKey: fileContentsCacheKey(
          file.filePath,
          "new",
          file.modifiedContent
        ),
      }),
      [file.filePath, file.modifiedContent, file.treePath]
    )
    const lastSlash = file.treePath.lastIndexOf("/")
    const directory =
      lastSlash === -1 ? "" : file.treePath.slice(0, lastSlash + 1)
    const fileName = file.treePath.slice(lastSlash + 1)

    return (
      <div ref={sectionRef} className="overflow-hidden border-b border-border">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center gap-2 bg-card px-3 py-2 text-left text-xs transition-colors hover:bg-accent"
        >
          <CaretDownIcon
            className={cn(
              "size-3 shrink-0 text-muted-foreground transition-transform",
              !open && "-rotate-90"
            )}
          />
          <span className="min-w-0 truncate" title={file.treePath}>
            {directory && (
              <span className="text-muted-foreground">{directory}</span>
            )}
            <span className="font-medium text-foreground">{fileName}</span>
          </span>
          <span className="ml-auto flex shrink-0 items-center gap-2">
            <span className="text-success-foreground">+{file.additions}</span>
            <span className="text-destructive">-{file.deletions}</span>
          </span>
        </button>
        {open &&
          (file.unrenderable ? (
            file.patch ? (
              <pre className="overflow-x-auto bg-background p-4 font-mono text-xs leading-5 text-foreground">
                <code>{file.patch}</code>
              </pre>
            ) : (
              <div className="bg-background p-4 text-center text-xs text-muted-foreground/70">
                Binary file — diff not available.
              </div>
            )
          ) : (
            <div
              className="overflow-hidden bg-background"
              style={
                {
                  "--panel-diff-bg": "var(--background)",
                } as React.CSSProperties
              }
            >
              <MultiFileDiff
                oldFile={oldFile}
                newFile={newFile}
                options={diffOptions}
                metrics={DIFF_VIRTUAL_METRICS}
              />
            </div>
          ))}
      </div>
    )
  },
  (prev, next) => prev.file === next.file
)

function FileTreeExplorer({
  files,
  selectedTreePath,
  onSelect,
}: {
  files: Array<PanelFile>
  selectedTreePath: string | null
  onSelect: (path: string) => void
}) {
  const paths = useMemo(() => files.map((file) => file.treePath), [files])
  const gitStatus = useMemo<Array<GitStatusEntry>>(
    () => files.map((file) => ({ path: file.treePath, status: file.status })),
    [files]
  )

  const { model } = useFileTree({
    paths,
    gitStatus,
    initialExpansion: "open",
    flattenEmptyDirectories: true,
    search: true,
    icons: "complete",
    unsafeCSS: TREE_UNSAFE_CSS,
  })

  useEffect(() => {
    model.resetPaths(paths)
  }, [model, paths])

  useEffect(() => {
    model.setGitStatus(gitStatus)
  }, [model, gitStatus])

  const selection = useFileTreeSelection(model)
  useEffect(() => {
    const path = selection[0]
    if (path) onSelect(path)
  }, [selection, onSelect])

  useEffect(() => {
    if (selectedTreePath) {
      model.scrollToPath(selectedTreePath, { focus: false })
    }
  }, [model, selectedTreePath])

  return (
    <div className="flex h-full flex-col">
      <FileTree model={model} style={{ height: "100%", ...treeThemeStyle() }} />
    </div>
  )
}
