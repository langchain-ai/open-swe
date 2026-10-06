import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  MultiFileDiff,
  Virtualizer,
  WorkerPoolContextProvider,
} from "@pierre/diffs/react"
import {
  FileTree,
  useFileTree,
  useFileTreeSelection,
} from "@pierre/trees/react"
import type { FileContents } from "@pierre/diffs/react"
import { useDiffLineSelection } from "@/features/agents/utils/diffSelection"
import {
  selectionExcerpts,
  serializeExcerpts,
} from "@/features/agents/utils/codeExcerpt"
import { DiffSelectionPopover } from "@/features/agents/components/DiffSelectionPopover"
import { reportError } from "@/lib/errorReporting"
import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  Collapsible,
  CollapsibleChevron,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@langchain/gtm-platform-design-system/ui/collapsible"
import { FadeText } from "@langchain/gtm-platform-design-system/ui/fade-text"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"
import type { GitStatus, GitStatusEntry } from "@pierre/trees"

import type { ThreadPrDiffFile } from "@/features/agents/lib/api"
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

// The tree tints filenames by git status; the changed-files list states status
// with its diffstat instead, so names stay on neutral ink.
const TREE_FILE_FG = "var(--gtm-ink-muted)"

// Selected rows read as the sidebar's selection: fill plus weight. The built-in
// git-status content colour outranks the selection colour by specificity, so
// override it from the `unsafe` layer.
export const TREE_UNSAFE_CSS = `
  [data-item-selected="true"] [data-item-section="content"] {
    color: var(--trees-selected-fg);
    font-weight: 500;
  }

  /* On click a row is focus-ringed a frame before it's marked selected, which
   * flashes the accent outline. Pointer focus doesn't match :focus-visible, so
   * drop the ring there; keyboard navigation keeps it. */
  [data-item-focused="true"]:not(:focus-visible)::before {
    outline-color: transparent;
  }
`

/** SidebarTree row geometry: the compact control rung. */
export const TREE_ITEM_HEIGHT = 28

export function treeThemeStyle(): React.CSSProperties {
  return {
    "--trees-theme-sidebar-bg": "var(--gtm-panel)",
    "--trees-theme-sidebar-fg": "var(--gtm-ink)",
    "--trees-theme-sidebar-border": "var(--gtm-line)",
    "--trees-theme-sidebar-header-fg": "var(--gtm-ink-subtle)",
    "--trees-theme-list-hover-bg": "var(--gtm-hover)",
    "--trees-theme-list-active-selection-bg": "var(--gtm-selected)",
    "--trees-theme-list-active-selection-fg": "var(--gtm-ink)",
    "--trees-selected-focused-border-color-override": "transparent",
    "--trees-theme-input-bg": "var(--gtm-panel)",
    "--trees-theme-input-fg": "var(--gtm-ink)",
    "--trees-theme-input-border": "var(--gtm-line-strong)",
    "--trees-theme-focus-ring": "var(--gtm-primary)",
    "--trees-theme-scrollbar-thumb": "var(--gtm-line-strong)",
    "--trees-accent-override": "var(--gtm-primary)",
    "--trees-indent-guide-bg-override": "var(--gtm-line-strong)",
    "--trees-font-family-override":
      "var(--font-inter), ui-sans-serif, system-ui, sans-serif",
    "--trees-font-size-override": "13px",
    // rounded-compact; the tree draws its own rows, so the rung is restated.
    "--trees-border-radius-override": "12px",
    "--trees-padding-inline-override": "8px",
    "--trees-theme-git-added-fg": TREE_FILE_FG,
    "--trees-theme-git-modified-fg": TREE_FILE_FG,
    "--trees-theme-git-deleted-fg": TREE_FILE_FG,
    "--trees-theme-git-renamed-fg": TREE_FILE_FG,
    "--trees-theme-git-untracked-fg": TREE_FILE_FG,
    "--trees-theme-git-ignored-fg": "var(--gtm-ink-subtle)",
  } as React.CSSProperties
}

/** A file's +/- counts, in the state inks. */
export function DiffStat(props: { additions: number; deletions: number }) {
  return (
    <Inline className="shrink-0 font-mono tabular-nums">
      <Badge tier="plain" tone="positive" className="font-mono">
        +{props.additions}
      </Badge>
      <Badge tier="plain" tone="risk" className="font-mono">
        -{props.deletions}
      </Badge>
    </Inline>
  )
}

interface DiffFilesViewProps {
  files: Array<PanelFile>
  onComment?: (content: string) => Promise<void>
  /** Path to select and scroll to, set when a transcript row is clicked. */
  revealFilePath?: string | null
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
  revealFilePath,
  fullScreen,
  emptyLabel,
  truncated,
  hideHeader,
  leading,
  actions,
  onComment,
}: DiffFilesViewProps) {
  const isMobile = useIsMobile()
  const [selectedTreePath, setSelectedTreePath] = useState<string | null>(null)
  const sectionRefs = useRef<Record<string, HTMLDivElement | null>>({})

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
  const selectTreePath = useCallback((path: string) => {
    setSelectedTreePath(path)
    const target = filesRef.current.find((file) => file.treePath === path)
    if (!target) return
    sectionRefs.current[target.filePath]?.scrollIntoView({
      block: "start",
      behavior: "smooth",
    })
  }, [])

  useEffect(() => {
    if (!revealFilePath) return
    // Transcript rows carry absolute paths; diff files are repo-relative.
    const target = filesRef.current.find(
      (file) =>
        file.filePath === revealFilePath ||
        revealFilePath.endsWith(`/${file.filePath}`)
    )
    if (target) selectTreePath(target.treePath)
  }, [revealFilePath, files, selectTreePath])

  return (
    <>
      {!hideHeader && (
        <Inline
          gap="xs"
          className="@container min-h-row-data shrink-0 flex-nowrap overflow-hidden border-b border-line px-2"
        >
          <Box className="min-w-0 flex-1">{leading}</Box>
          <Inline gap="xs" className="ml-auto shrink-0">
            <DiffWrapToggle />
            {actions}
            {files.length > 0 && (
              <Inline gap="xs" className="shrink-0 pl-1 whitespace-nowrap">
                <span
                  className="text-meta text-ink-subtle @max-xl:hidden"
                  title={
                    truncated ? "Only the first files are shown" : undefined
                  }
                >
                  {truncated ? "first " : ""}
                  {files.length} file{files.length === 1 ? "" : "s"}
                </span>
                <DiffStat
                  additions={totals.additions}
                  deletions={totals.deletions}
                />
              </Inline>
            )}
          </Inline>
        </Inline>
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
              {files.map((file) => (
                <FileDiffSection
                  key={file.filePath}
                  file={file}
                  onComment={onComment}
                  sectionRef={(node) => {
                    sectionRefs.current[file.filePath] = node
                  }}
                />
              ))}
            </Virtualizer>
          </WorkerPoolContextProvider>
        ) : (
          <Box className="flex min-h-0 flex-1 flex-col overflow-y-auto">
            <EmptyState title={emptyLabel} />
          </Box>
        )}

        {fullScreen && !isMobile && files.length > 0 && (
          <Box bg="panel" className="w-72 shrink-0 border-l border-line">
            <FileTreeExplorer
              files={files}
              selectedTreePath={selectedTreePath}
              onSelect={selectTreePath}
            />
          </Box>
        )}
      </div>
    </>
  )
}

const FileDiffSection = memo(
  function FileDiffSection({
    file,
    sectionRef,
    onComment,
  }: {
    file: PanelFile
    onComment?: (content: string) => Promise<void>
    sectionRef: (node: HTMLDivElement | null) => void
  }) {
    const [open, setOpen] = useState(true)
    const diffOptions = useDiffOptions()
    const textareaRef = useRef<HTMLTextAreaElement>(null)
    const lineSelection = useDiffLineSelection({ enabled: Boolean(onComment) })
    const draft = lineSelection.committed?.range ?? null
    const [comment, setComment] = useState("")
    const [sending, setSending] = useState(false)
    const [selectedFile, setSelectedFile] = useState(file)
    if (
      selectedFile.originalContent !== file.originalContent ||
      selectedFile.modifiedContent !== file.modifiedContent
    ) {
      setSelectedFile(file)
      lineSelection.close()
    }
    const options = useMemo(
      () => ({ ...diffOptions, ...lineSelection.diffOptions }),
      [diffOptions, lineSelection.diffOptions]
    )
    const submitComment = async () => {
      if (!draft || !onComment || !comment.trim() || sending) return
      setSending(true)
      try {
        await onComment(
          serializeExcerpts(
            comment,
            selectionExcerpts(file.filePath, file, draft)
          )
        )
        lineSelection.close()
        setComment("")
      } catch (error) {
        reportError({ title: "Couldn't send the diff comment", error })
      } finally {
        setSending(false)
      }
    }
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
      <div ref={sectionRef} className="overflow-hidden border-b border-line">
        <Collapsible open={open} onOpenChange={setOpen}>
          <CollapsibleTrigger className="h-row-data w-full gap-2 rounded-none bg-panel px-3 transition-colors duration-fast ease-out-quint hover:bg-hover focus-visible:ring-inset motion-reduce:transition-none">
            <CollapsibleChevron />
            <FadeText
              lines={1}
              render={<span title={file.treePath} />}
              className="flex-1 font-mono text-label whitespace-nowrap"
            >
              {directory && (
                <span className="text-ink-subtle">{directory}</span>
              )}
              <span className="font-medium text-ink">{fileName}</span>
            </FadeText>
            <DiffStat additions={file.additions} deletions={file.deletions} />
          </CollapsibleTrigger>
          <CollapsibleContent smooth={false}>
            {file.unrenderable ? (
              file.patch ? (
                <pre className="overflow-x-auto bg-panel p-4 font-mono text-label text-ink">
                  <code>{file.patch}</code>
                </pre>
              ) : (
                <p className="bg-panel p-4 text-center text-meta text-ink-subtle">
                  Binary file — diff not available.
                </p>
              )
            ) : (
              <div
                {...lineSelection.wrapperProps}
                className="overflow-hidden bg-panel"
              >
                <MultiFileDiff
                  oldFile={oldFile}
                  newFile={newFile}
                  options={options}
                  selectedLines={lineSelection.selectedLines}
                  metrics={DIFF_VIRTUAL_METRICS}
                />
              </div>
            )}
          </CollapsibleContent>
        </Collapsible>
        {onComment && (
          <DiffSelectionPopover
            selection={lineSelection}
            initialFocus={textareaRef}
            className="w-80"
          >
            <Stack
              render={<form />}
              gap="sm"
              onSubmit={(event) => {
                event.preventDefault()
                void submitComment()
              }}
            >
              {draft && (
                <div className="truncate font-mono text-meta text-ink-subtle">
                  {file.treePath} ·{" "}
                  {draft.start === draft.end
                    ? `line ${draft.start}`
                    : `lines ${Math.min(draft.start, draft.end)}–${Math.max(draft.start, draft.end)}`}
                </div>
              )}
              <Textarea
                ref={textareaRef}
                aria-label="Comment on selected code"
                placeholder="Ask Open SWE about these lines…"
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                onKeyDown={(event) => {
                  if (
                    event.key === "Enter" &&
                    !event.shiftKey &&
                    !event.nativeEvent.isComposing
                  ) {
                    event.preventDefault()
                    void submitComment()
                  }
                }}
                disabled={sending}
                className="max-h-48 min-h-20"
              />
              <Inline gap="sm" justify="end">
                <Button
                  type="button"
                  variant="ghost"
                  size="compact"
                  disabled={sending}
                  onClick={() => {
                    lineSelection.close()
                    setComment("")
                  }}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  size="compact"
                  disabled={sending || !comment.trim()}
                >
                  {sending ? "Sending…" : "Send to Open SWE"}
                </Button>
              </Inline>
            </Stack>
          </DiffSelectionPopover>
        )}
      </div>
    )
  },
  (prev, next) => prev.file === next.file && prev.onComment === next.onComment
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
    itemHeight: TREE_ITEM_HEIGHT,
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
    <Stack className="h-full">
      <FileTree model={model} style={{ height: "100%", ...treeThemeStyle() }} />
    </Stack>
  )
}
