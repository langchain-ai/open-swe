import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Virtualizer, WorkerPoolContextProvider } from "@pierre/diffs/react"
import {
  FileTree,
  useFileTree,
  useFileTreeSelection,
} from "@pierre/trees/react"
import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { CaretDownIcon } from "@phosphor-icons/react/dist/ssr/CaretDown"
import type { FileContents } from "@pierre/diffs/react"
import { useDiffLineSelection } from "@/features/agents/utils/diffSelection"
import {
  selectionExcerpts,
  serializeExcerpts,
} from "@/features/agents/utils/codeExcerpt"
import { DiffSelectionPopover } from "@/features/agents/components/DiffSelectionPopover"
import { reportError } from "@/lib/errorReporting"
import type { GitStatus, GitStatusEntry } from "@pierre/trees"

import type { ThreadPrDiffFile } from "@/features/agents/lib/api"
import { DiffWrapToggle } from "@/features/agents/components/DiffWrapToggle"
import { ScopedFileDiff } from "@/features/agents/components/ScopedFileDiff"
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

// The tree tints filename text by git status; feeding a neutral foreground
// keeps names grey instead of accent-blue.
const TREE_FILE_FG = "var(--text-secondary)"

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
    "--trees-theme-sidebar-bg": "var(--bg-surface-level-1)",
    "--trees-theme-sidebar-fg": "var(--text-primary)",
    "--trees-theme-sidebar-border": "var(--border-default)",
    "--trees-theme-sidebar-header-fg": "var(--text-secondary)",
    "--trees-theme-list-hover-bg": "var(--bg-surface-level-1-hover)",
    "--trees-theme-list-active-selection-bg": "var(--bg-selected)",
    "--trees-theme-list-active-selection-fg": "var(--text-primary)",
    "--trees-selected-focused-border-color-override": "transparent",
    "--trees-theme-input-bg": "var(--bg-surface-level-1)",
    "--trees-theme-input-fg": "var(--text-primary)",
    "--trees-theme-input-border": "var(--border-default)",
    "--trees-theme-focus-ring": "var(--border-focus)",
    "--trees-theme-scrollbar-thumb": "var(--border-default)",
    "--trees-theme-git-added-fg": TREE_FILE_FG,
    "--trees-theme-git-modified-fg": TREE_FILE_FG,
    "--trees-theme-git-deleted-fg": TREE_FILE_FG,
    "--trees-theme-git-renamed-fg": TREE_FILE_FG,
    "--trees-theme-git-untracked-fg": TREE_FILE_FG,
    "--trees-theme-git-ignored-fg": "var(--text-tertiary)",
  } as React.CSSProperties
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
        <div className="@container flex min-h-9 flex-nowrap items-center gap-1 overflow-hidden border-b border-default px-3 py-1">
          <div className="min-w-0 flex-1">{leading}</div>
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <DiffWrapToggle />
            {actions}
            {files.length > 0 && (
              <span className="flex shrink-0 items-center gap-2 text-[11px] whitespace-nowrap text-tertiary">
                <span
                  className="@max-[620px]:hidden"
                  title={
                    truncated ? "Only the first files are shown" : undefined
                  }
                >
                  {truncated ? "first " : ""}
                  {files.length} file{files.length === 1 ? "" : "s"}
                </span>
                <span className="text-success-secondary">
                  +{totals.additions}
                </span>
                <span className="text-error-secondary">
                  -{totals.deletions}
                </span>
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
          <div className="min-h-0 flex-1 overflow-y-auto p-6 text-center text-xs text-tertiary">
            {emptyLabel}
          </div>
        )}

        {fullScreen && !isMobile && files.length > 0 && (
          <div className="w-72 shrink-0 border-l border-default bg-surface-level-1">
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
      <div ref={sectionRef} className="overflow-hidden border-b border-default">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center gap-2 bg-surface-level-1 px-3 py-2 text-left text-xs transition-colors hover:bg-surface-level-1-hover"
        >
          <CaretDownIcon
            weight="regular"
            className={cn(
              "size-3 shrink-0 text-icon-secondary transition-transform",
              !open && "-rotate-90"
            )}
          />
          <span className="min-w-0 truncate" title={file.treePath}>
            {directory && <span className="text-secondary">{directory}</span>}
            <span className="font-medium text-primary">{fileName}</span>
          </span>
          <span className="ml-auto flex shrink-0 items-center gap-2">
            <span className="text-success-secondary">+{file.additions}</span>
            <span className="text-error-secondary">-{file.deletions}</span>
          </span>
        </button>
        {open &&
          (file.unrenderable ? (
            file.patch ? (
              <pre className="overflow-x-auto bg-surface-level-1 p-4 font-mono text-xs leading-5 text-primary">
                <code>{file.patch}</code>
              </pre>
            ) : (
              <div className="bg-surface-level-1 p-4 text-center text-xs text-tertiary">
                Binary file — diff not available.
              </div>
            )
          ) : (
            <div
              {...lineSelection.wrapperProps}
              className="overflow-hidden bg-surface-level-1"
              style={
                {
                  "--panel-diff-bg": "var(--bg-surface-level-1)",
                } as React.CSSProperties
              }
            >
              <ScopedFileDiff
                oldFile={oldFile}
                newFile={newFile}
                options={options}
                selectedLines={lineSelection.selectedLines}
                metrics={DIFF_VIRTUAL_METRICS}
              />
            </div>
          ))}
        {onComment && (
          <DiffSelectionPopover
            selection={lineSelection}
            initialFocus={textareaRef}
            className="w-80 p-2"
          >
            <form
              className="flex flex-col gap-space-2"
              onSubmit={(event) => {
                event.preventDefault()
                void submitComment()
              }}
            >
              {draft && (
                <div className="truncate text-xs text-secondary">
                  {file.treePath} ·{" "}
                  {draft.start === draft.end
                    ? `line ${draft.start}`
                    : `lines ${Math.min(draft.start, draft.end)}–${Math.max(draft.start, draft.end)}`}
                </div>
              )}
              <Textarea
                ref={textareaRef}
                size="md"
                aria-label="Comment on selected code"
                placeholder="Ask Open SWE about these lines…"
                value={comment}
                onChange={setComment}
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
                autoResize
                maxHeight={192}
                inputClassName="min-h-16"
              />
              <div className="flex justify-end gap-space-2">
                <Button
                  size="xs"
                  color="secondary"
                  variant="plain"
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
                  size="xs"
                  disabled={sending || !comment.trim()}
                >
                  {sending ? "Sending…" : "Send to Open SWE"}
                </Button>
              </div>
            </form>
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
