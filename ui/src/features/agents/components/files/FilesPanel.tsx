import { useMemo, useState } from "react"
import { TreeStructureIcon } from "@phosphor-icons/react"
import {
  File,
  Virtualizer,
  WorkerPoolContextProvider,
} from "@pierre/diffs/react"

import type { TerminalTarget } from "@/features/agents/lib/terminalSession"
import { DiffWrapToggle } from "@/features/agents/components/DiffWrapToggle"
import { FileBrowserPanel } from "@/features/agents/components/files/FileBrowserPanel"
import {
  DIFF_UNSAFE_CSS,
  DIFF_VIRTUALIZER_CONFIG,
  DIFF_WORKER_HIGHLIGHTER_OPTIONS,
  DIFF_WORKER_POOL_OPTIONS,
  diffOptions,
  fileContentsCacheKey,
  useDiffOverflow,
} from "@/features/agents/utils/diffUtils"
import { useWorkspaceFile } from "@/features/agents/lib/workspaceFiles"
import { useResolvedTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

interface FilesPanelProps {
  target: TerminalTarget
  /** File open in the surface; `null` shows the explorer alone. */
  relativePath: string | null
  revealRequestId: number
  onOpenFile: (relativePath: string) => void
}

function PreviewMessage(props: { children: string; error?: boolean }) {
  return (
    <div
      className={cn(
        "flex min-h-0 flex-1 items-center justify-center px-6 text-center text-xs text-muted-foreground",
        props.error && "text-destructive"
      )}
    >
      {props.children}
    </div>
  )
}

function SourcePreview(props: { name: string; contents: string }) {
  const themeType = useResolvedTheme()
  const [overflow] = useDiffOverflow()
  const file = useMemo(
    () => ({
      name: props.name,
      contents: props.contents,
      cacheKey: fileContentsCacheKey(props.name, "new", props.contents),
    }),
    [props.contents, props.name]
  )
  return (
    <WorkerPoolContextProvider
      poolOptions={DIFF_WORKER_POOL_OPTIONS}
      highlighterOptions={DIFF_WORKER_HIGHLIGHTER_OPTIONS}
    >
      <Virtualizer
        className="min-h-0 flex-1 overflow-auto"
        config={DIFF_VIRTUALIZER_CONFIG}
      >
        <File
          file={file}
          options={{
            theme: diffOptions.theme,
            themeType,
            overflow,
            disableFileHeader: true,
            unsafeCSS: DIFF_UNSAFE_CSS,
          }}
        />
      </Virtualizer>
    </WorkerPoolContextProvider>
  )
}

function FilePreview(props: {
  file: ReturnType<typeof useWorkspaceFile>
  relativePath: string
}) {
  const { data, error } = props.file
  if (error) return <PreviewMessage error>{error.message}</PreviewMessage>
  if (!data) return <PreviewMessage>Loading…</PreviewMessage>
  if (data.kind !== "file") return <PreviewMessage>Not a file.</PreviewMessage>
  if (data.binary)
    return <PreviewMessage>Binary file — preview not available.</PreviewMessage>
  return (
    <>
      {data.truncated ? (
        <div className="shrink-0 border-b border-border px-3 py-1.5 text-[11px] text-muted-foreground">
          Preview limited to the first 1 MB of a {data.size.toLocaleString()}{" "}
          byte file.
        </div>
      ) : null}
      <SourcePreview name={props.relativePath} contents={data.contents} />
    </>
  )
}

/** Read-only workspace file preview with the file explorer beside it. */
export function FilesPanel({
  target,
  relativePath,
  revealRequestId,
  onOpenFile,
}: FilesPanelProps) {
  const [explorerOpen, setExplorerOpen] = useState(true)
  const file = useWorkspaceFile(target, relativePath)
  const showExplorer = explorerOpen || relativePath === null

  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background"
      style={{ "--panel-diff-bg": "var(--background)" } as React.CSSProperties}
    >
      {relativePath ? (
        <div className="flex h-9 shrink-0 items-center gap-1 border-b border-border px-3">
          <span
            className="min-w-0 flex-1 truncate text-xs text-muted-foreground"
            title={relativePath}
          >
            {relativePath}
          </span>
          <DiffWrapToggle />
          <button
            type="button"
            aria-label={
              explorerOpen ? "Hide file explorer" : "Show file explorer"
            }
            aria-pressed={explorerOpen}
            title={explorerOpen ? "Hide file explorer" : "Show file explorer"}
            onClick={() => setExplorerOpen((open) => !open)}
            className={cn(
              "flex size-6 items-center justify-center rounded text-muted-foreground/70 transition-colors hover:text-foreground",
              explorerOpen && "bg-accent text-foreground"
            )}
          >
            <TreeStructureIcon className="size-3.5" />
          </button>
        </div>
      ) : null}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {relativePath ? (
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
            <FilePreview file={file} relativePath={relativePath} />
          </div>
        ) : null}
        {showExplorer ? (
          <aside
            className={cn(
              "flex min-h-0 shrink-0",
              relativePath
                ? "w-[min(22rem,46%)] min-w-56 border-l border-border"
                : "min-w-0 flex-1"
            )}
          >
            <FileBrowserPanel
              target={target}
              selectedPath={relativePath}
              selectedPathRevealId={revealRequestId}
              onOpenFile={onOpenFile}
              {...(relativePath
                ? { onRefreshSelectedFile: () => void file.refetch() }
                : {})}
            />
          </aside>
        ) : null}
      </div>
    </div>
  )
}
