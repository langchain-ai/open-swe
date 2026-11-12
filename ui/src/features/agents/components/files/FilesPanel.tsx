import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react"
import type { ComponentType, LazyExoticComponent } from "react"
import { TreeStructureIcon } from "@phosphor-icons/react/dist/ssr/TreeStructure"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
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
  DownloadFallback,
  DownloadOnError,
} from "@/features/agents/components/files/previews/DownloadFallback"
import { PreviewMessage } from "@/features/agents/components/files/previews/PreviewMessage"
import {
  DIFF_UNSAFE_CSS,
  DIFF_VIRTUALIZER_CONFIG,
  DIFF_WORKER_HIGHLIGHTER_OPTIONS,
  DIFF_WORKER_POOL_OPTIONS,
  diffOptions,
  fileContentsCacheKey,
  useDiffOverflow,
} from "@/features/agents/utils/diffUtils"
import {
  useWorkspaceFile,
  useWorkspacePreview,
} from "@/features/agents/lib/workspaceFiles"
import {
  hasSourceView,
  previewKind,
  usePreviewMode,
  type PreviewKind,
  type PreviewMode,
  type ViewerProps,
} from "@/features/agents/lib/filePreview"
import { ARTIFACT_SANDBOX } from "@/features/agents/lib/artifactShell"
import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import {
  SelectionReply,
  codePassage,
} from "@/features/agents/components/SelectionReply"
import type { PassageReply } from "@/features/agents/utils/passageReply"
import { useResolvedTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

interface FilesPanelProps {
  target: TerminalTarget
  /** File open in the surface; `null` shows the explorer alone. */
  relativePath: string | null
  revealRequestId: number
  /** Whether the thread's run is going; the open file refreshes when it ends. */
  running?: boolean
  onOpenFile: (relativePath: string) => void
  /** Offers Reply on text selected in the open file. */
  onReply?: (reply: PassageReply) => void
}

function SourcePreview(props: {
  name: string
  contents: string
  onReply?: (reply: PassageReply) => void
}) {
  const themeType = useResolvedTheme()
  const paneRef = useRef<HTMLDivElement>(null)
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
      <div ref={paneRef} className="flex min-h-0 flex-1 flex-col">
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
      </div>
      {props.onReply && (
        <SelectionReply
          containerRef={paneRef}
          readPassage={codePassage(props.name, props.contents)}
          onReply={props.onReply}
        />
      )}
    </WorkerPoolContextProvider>
  )
}

function TruncatedBanner(props: { size: number }) {
  return (
    <div className="shrink-0 border-b border-default px-space-3 py-space-1 text-xxs text-secondary">
      Preview limited to the first 1 MB of a {props.size.toLocaleString()} byte
      file.
    </div>
  )
}

/** A file read as text: its source, or Markdown rendered. */
function TextFile(props: {
  target: TerminalTarget
  file: ReturnType<typeof useWorkspaceFile>
  link: ReturnType<typeof useWorkspacePreview>
  relativePath: string
  markdown: boolean
  onOpenFile: (relativePath: string) => void
  onReply?: (reply: PassageReply) => void
}) {
  const { data, error } = props.file
  if (error) return <PreviewMessage error>{error.message}</PreviewMessage>
  // Without its link (an older desktop app), Markdown still renders, minus relative images.
  if (!data || (props.markdown && props.link.isPending))
    return <PreviewMessage>Loading…</PreviewMessage>
  if (data.kind !== "file") return <PreviewMessage>Not a file.</PreviewMessage>
  if (data.binary)
    return (
      <DownloadFallback
        target={props.target}
        relativePath={props.relativePath}
        message="Binary file — no preview."
      />
    )
  return (
    <>
      {data.truncated ? <TruncatedBanner size={data.size} /> : null}
      {props.markdown ? (
        <div className="min-h-0 flex-1 overflow-auto px-space-6 py-space-4">
          <Markdown
            // A refreshed link must reach the images, which Streamdown's memo would keep.
            key={props.link.data?.url}
            content={data.contents}
            enlargeImages
            workspacePath={props.relativePath}
            // The document's own preview link grants its folder.
            imageBase={props.link.data?.url}
            onOpenFile={props.onOpenFile}
          />
        </div>
      ) : (
        <SourcePreview
          name={props.relativePath}
          contents={data.contents}
          onReply={props.onReply}
        />
      )}
    </>
  )
}

const LAZY_VIEWERS: Partial<
  Record<PreviewKind, LazyExoticComponent<ComponentType<ViewerProps>>>
> = {
  table: lazy(() => import("./previews/TablePreview")),
  notebook: lazy(() => import("./previews/NotebookPreview")),
  document: lazy(() => import("./previews/DocumentPreview")),
  spreadsheet: lazy(() => import("./previews/SpreadsheetPreview")),
}

function RenderedPreview(props: {
  kind: PreviewKind
  target: TerminalTarget
  link: ReturnType<typeof useWorkspacePreview>
  relativePath: string
  onOpenFile: (relativePath: string) => void
}) {
  const { data, error } = props.link
  // The link a media element failed to play: its contents don't match its extension.
  const [unplayable, setUnplayable] = useState<string | null>(null)
  if (error) return <PreviewMessage error>{error.message}</PreviewMessage>
  if (!data) return <PreviewMessage>Loading…</PreviewMessage>
  if (unplayable === data.url)
    return (
      <DownloadFallback
        target={props.target}
        relativePath={props.relativePath}
        message="This file can't be previewed."
      />
    )
  const failed = () => setUnplayable(data.url)
  const Viewer = LAZY_VIEWERS[props.kind]
  if (Viewer)
    return (
      <DownloadOnError
        key={data.url}
        target={props.target}
        relativePath={props.relativePath}
      >
        <Suspense fallback={<PreviewMessage>Loading…</PreviewMessage>}>
          <Viewer
            url={data.url}
            path={props.relativePath}
            onOpenFile={props.onOpenFile}
          />
        </Suspense>
      </DownloadOnError>
    )
  if (props.kind === "video")
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center bg-surface-level-2 p-space-4">
        <video
          key={data.url}
          src={data.url}
          controls
          onError={failed}
          className="max-h-full max-w-full"
        />
      </div>
    )
  if (props.kind === "audio")
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-space-4">
        <audio
          key={data.url}
          src={data.url}
          controls
          onError={failed}
          className="w-full"
        />
      </div>
    )
  if (props.kind === "pdf")
    return (
      // No sandbox attribute: Chromium's PDF viewer refuses a sandboxed frame.
      // The response's own `CSP: sandbox` still sandboxes the document.
      <iframe
        key={data.url}
        title={props.relativePath}
        src={data.url}
        referrerPolicy="no-referrer"
        className="block min-h-0 w-full flex-1 border-0"
      />
    )
  if (props.kind === "html")
    return (
      <SandboxedHtmlFrame
        key={data.url}
        title={props.relativePath}
        src={data.url}
        sandbox={ARTIFACT_SANDBOX}
        className="min-h-0 flex-1"
      />
    )
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-space-4">
      {/* SVG only ever through <img>, which never runs its scripts. */}
      <img
        src={data.url}
        alt={props.relativePath}
        onError={failed}
        className="max-h-full max-w-full object-contain"
      />
    </div>
  )
}

/** Read-only workspace file preview with the file explorer beside it. */
export function FilesPanel({
  target,
  relativePath,
  revealRequestId,
  running = false,
  onOpenFile,
  onReply,
}: FilesPanelProps) {
  const [explorerOpen, setExplorerOpen] = useState(true)
  const kind = previewKind(relativePath ?? "")
  const [mode, setMode] = usePreviewMode(kind)
  const showSource =
    kind === "source" || (hasSourceView(kind) && mode === "source")
  const needsText = showSource || kind === "markdown"
  const needsLink = !showSource && kind !== "download"
  const file = useWorkspaceFile(target, needsText ? relativePath : null)
  const link = useWorkspacePreview(target, relativePath, needsLink)
  const showExplorer = explorerOpen || relativePath === null

  const refresh = () => {
    if (needsText) void file.refetch()
    if (needsLink) void link.refetch()
  }
  // Reopening the file, or the run that may have rewritten it finishing, shows it afresh.
  const seen = useRef({ revealRequestId, running })
  useEffect(() => {
    const previous = seen.current
    seen.current = { revealRequestId, running }
    if (
      previous.revealRequestId !== revealRequestId ||
      (previous.running && !running)
    )
      refresh()
  })

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
          {hasSourceView(kind) ? (
            <GroupedTabs<PreviewMode>
              size="xs"
              value={mode}
              onChange={setMode}
              options={[
                { value: "rendered", display: "Preview" },
                { value: "source", display: "Source" },
              ]}
            />
          ) : null}
          {showSource ? <DiffWrapToggle /> : null}
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
            {kind === "download" ? (
              <DownloadFallback
                target={target}
                relativePath={relativePath}
                message="No preview for this file type."
              />
            ) : needsText ? (
              <TextFile
                target={target}
                file={file}
                link={link}
                relativePath={relativePath}
                markdown={!showSource}
                onOpenFile={onOpenFile}
                onReply={onReply}
              />
            ) : (
              <RenderedPreview
                kind={kind}
                target={target}
                link={link}
                relativePath={relativePath}
                onOpenFile={onOpenFile}
              />
            )}
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
              {...(relativePath ? { onRefreshSelectedFile: refresh } : {})}
            />
          </aside>
        ) : null}
      </div>
    </div>
  )
}
