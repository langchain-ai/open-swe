import { useCallback, useMemo, useRef, useState } from "react"
import { ChevronDown, Download, MessageSquareQuote } from "lucide-react"
import { File as PierreFile, PatchDiff } from "@pierre/diffs/react"
import type { SelectedLineRange } from "@pierre/diffs/react"

import { openDownload } from "./OutputIframe"
import type { ShowUserDisplay } from "@/features/agents/lib/types"
import {
  ARTIFACT_ALLOW,
  ARTIFACT_SANDBOX,
} from "@/features/agents/lib/artifactShell"
import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"
import {
  insertIntoComposer,
  quoteFileLines,
} from "@/features/agents/lib/composerInsert"
import {
  selectPatchLines,
  splitPatch,
} from "@/features/agents/lib/patchSelection"
import { Markdown } from "./Markdown"
import { useDiffOptions } from "@/features/agents/utils/diffUtils"
import { Button, IconButton } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const BODY_MAX_HEIGHT = 520
const IFRAME_HEIGHT = 480

interface Selection {
  file: number
  range: SelectedLineRange
}

function orderedRange(range: SelectedLineRange): {
  start: number
  end: number
} {
  return {
    start: Math.min(range.start, range.end),
    end: Math.max(range.start, range.end),
  }
}

function mermaidFence(source: string): string {
  return "```mermaid\n" + source.replace(/```/g, "") + "\n```"
}

function fenceLanguage(filename: string): string {
  const extension = filename.split(".").pop() ?? ""
  return extension === filename ? "" : extension.toLowerCase()
}

export function ShowUserCard({ display }: { display: ShowUserDisplay }) {
  const [expanded, setExpanded] = useState(true)
  const [selection, setSelection] = useState<Selection | null>(() =>
    display.kind === "text"
      ? {
          file: 0,
          range: { start: display.startLine, end: display.endLine },
        }
      : null
  )
  const patchFiles = useMemo(
    () => (display.kind === "diff" ? splitPatch(display.content) : []),
    [display]
  )

  const quote = useCallback((): string | null => {
    if (!selection) return null
    const { start, end } = orderedRange(selection.range)
    if (display.kind === "text") {
      const lines = display.content.split("\n").slice(start - 1, end)
      return quoteFileLines(
        display.path,
        start,
        end,
        lines,
        fenceLanguage(display.filename)
      )
    }
    if (display.kind === "diff") {
      const file = patchFiles[selection.file]
      if (!file) return null
      const side = selection.range.side ?? "additions"
      const lines = selectPatchLines(file.patch, side, start, end)
      return quoteFileLines(
        file.path || display.path,
        start,
        end,
        lines,
        "diff"
      )
    }
    return null
  }, [display, patchFiles, selection])

  const handleComment = useCallback(() => {
    const text = quote()
    if (text) insertIntoComposer(text)
  }, [quote])

  const selectLines = useCallback(
    (file: number, range: SelectedLineRange | null) =>
      setSelection(range ? { file, range } : null),
    []
  )

  const rangeLabel = useMemo(() => {
    if (!selection) return null
    const { start, end } = orderedRange(selection.range)
    return start === end ? `L${start}` : `L${start}-${end}`
  }, [selection])

  const showPath = display.title !== display.path

  return (
    <section className="my-2 overflow-hidden rounded-lg border border-border bg-card">
      <header className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          <ChevronDown
            className={cn(
              "size-3.5 shrink-0 text-muted-foreground transition-transform",
              !expanded && "-rotate-90"
            )}
          />
          <span className="truncate text-xs font-medium text-foreground">
            {display.title}
          </span>
          {showPath && (
            <span className="truncate font-mono text-[11px] text-muted-foreground">
              {display.path}
            </span>
          )}
        </button>
        {display.kind === "html" && (
          <IconButton
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Download HTML"
            onClick={() => openDownload(display.downloadUrl)}
          >
            <Download />
          </IconButton>
        )}
        {(display.kind === "text" || display.kind === "diff") && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            disabled={!selection}
            title={
              selection
                ? "Quote the selected lines in the composer"
                : "Select lines to comment on them"
            }
            onClick={handleComment}
          >
            <MessageSquareQuote className="size-3.5" />
            {rangeLabel ? `Comment on ${rangeLabel}` : "Comment"}
          </Button>
        )}
      </header>
      {expanded && (
        <div
          className="overflow-auto border-t border-border bg-background"
          style={{ maxHeight: BODY_MAX_HEIGHT }}
        >
          {display.kind === "image" && (
            <img
              src={`data:${display.mimeType};base64,${display.contentBase64}`}
              alt={display.title}
              className="mx-auto block max-w-full object-contain"
              style={{ maxHeight: BODY_MAX_HEIGHT }}
            />
          )}
          {display.kind === "html" && (
            <SandboxedHtmlFrame
              title={display.title}
              src={display.previewUrl}
              sandbox={ARTIFACT_SANDBOX}
              allow={ARTIFACT_ALLOW}
              className="bg-background"
              style={{ height: IFRAME_HEIGHT }}
            />
          )}
          {display.kind === "diagram" && (
            <div className="px-3 py-2">
              <Markdown content={mermaidFence(display.content)} />
            </div>
          )}
          {display.kind === "markdown" && (
            <div className="px-3 py-2">
              <Markdown content={display.content} />
            </div>
          )}
          {display.kind === "text" && (
            <TextBody
              display={display}
              selection={selection?.range ?? null}
              onSelect={selectLines}
            />
          )}
          {display.kind === "diff" &&
            patchFiles.map((file, index) => (
              <DiffBody
                key={`${file.path}-${index}`}
                index={index}
                path={file.path}
                patch={file.patch}
                selection={selection?.file === index ? selection.range : null}
                onSelect={selectLines}
              />
            ))}
        </div>
      )}
    </section>
  )
}

interface BodyProps {
  selection: SelectedLineRange | null
  onSelect: (file: number, range: SelectedLineRange | null) => void
}

function TextBody({
  display,
  selection,
  onSelect,
}: BodyProps & { display: Extract<ShowUserDisplay, { kind: "text" }> }) {
  const diffOptions = useDiffOptions()
  const scrolledRef = useRef(false)
  const options = useMemo(
    () => ({
      theme: diffOptions.theme,
      themeType: diffOptions.themeType,
      overflow: diffOptions.overflow,
      unsafeCSS: diffOptions.unsafeCSS,
      disableFileHeader: true,
      enableLineSelection: true,
      onLineSelectionEnd: (range: SelectedLineRange | null) =>
        onSelect(0, range),
      onPostRender: (node: HTMLElement) => {
        if (scrolledRef.current || display.startLine <= 1) return
        const target = node.querySelector(
          `[data-column-number="${display.startLine}"]`
        )
        if (!target) return
        scrolledRef.current = true
        target.scrollIntoView({ block: "center" })
      },
    }),
    [diffOptions, display.startLine, onSelect]
  )
  const file = useMemo(
    () => ({ name: display.filename, contents: display.content }),
    [display.content, display.filename]
  )
  return (
    <div className="font-mono text-xs">
      <PierreFile file={file} options={options} selectedLines={selection} />
    </div>
  )
}

function DiffBody({
  index,
  path,
  patch,
  selection,
  onSelect,
}: BodyProps & { index: number; path: string; patch: string }) {
  const diffOptions = useDiffOptions()
  const options = useMemo(
    () => ({
      ...diffOptions,
      enableLineSelection: true,
      onLineSelectionEnd: (range: SelectedLineRange | null) =>
        onSelect(index, range),
    }),
    [diffOptions, index, onSelect]
  )
  return (
    <div className="font-mono text-xs">
      {path && (
        <div className="border-b border-border/60 bg-accent px-3 py-1 text-[11px] text-muted-foreground">
          {path}
        </div>
      )}
      <PatchDiff patch={patch} options={options} selectedLines={selection} />
    </div>
  )
}
