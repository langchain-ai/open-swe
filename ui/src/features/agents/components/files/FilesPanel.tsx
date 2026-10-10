import { useMemo, useState } from "react"
import { TreeStructureIcon } from "@phosphor-icons/react/dist/ssr/TreeStructure"
import { IconButton } from "@langchain/macaw-components/IconButton"
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
        "flex min-h-0 flex-1 items-center justify-center px-space-6 text-center text-xs text-secondary",
        props.error && "text-error-secondary"
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
        <div className="shrink-0 border-b border-default px-space-3 py-space-1 text-xxs text-secondary">
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
      className="flex min-h-0 flex-1 flex-col overflow-hidden bg-surface-level-1"
      style={
        {
          "--panel-diff-bg": "var(--bg-surface-level-1)",
        } as React.CSSProperties
      }
    >
      {relativePath ? (
        <div className="flex h-9 shrink-0 items-center gap-space-1 border-b border-default px-space-3">
          <span
            className="min-w-0 flex-1 truncate text-xs text-secondary"
            title={relativePath}
          >
            {relativePath}
          </span>
          <DiffWrapToggle />
          <IconButton
            icon={TreeStructureIcon}
            label={explorerOpen ? "Hide file explorer" : "Show file explorer"}
            size="sm"
            color="secondary"
            variant="plain"
            aria-pressed={explorerOpen}
            onClick={() => setExplorerOpen((open) => !open)}
            className={cn(explorerOpen && "bg-selected text-primary")}
          />
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
                ? "w-[min(22rem,46%)] min-w-56 border-l border-default"
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
