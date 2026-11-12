import Anser from "anser"
import type { ReactNode } from "react"

import { CodeBlock } from "@/features/agents/components/chat/CodeBlock"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"
import {
  ARTIFACT_ALLOW,
  ARTIFACT_CSP,
  ARTIFACT_SANDBOX,
  withArtifactShell,
} from "@/features/agents/lib/artifactShell"
import type { ViewerProps } from "@/features/agents/lib/filePreview"
import { usePreviewData } from "@/features/agents/lib/workspaceFiles"
import { useResolvedTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"
import { queryMessage } from "./PreviewMessage"

/** The parts of an nbformat 4 notebook the preview shows. */
interface NotebookOutput {
  output_type: string
  text?: string
  data: Record<string, string>
  traceback?: string
}

interface NotebookCell {
  cell_type: string
  source: string
  execution_count: number | null
  outputs: Array<NotebookOutput>
}

interface Notebook {
  language: string
  cells: Array<NotebookCell>
}

type JsonObject = Record<string, unknown>

const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif"] as const

// Only these get a color; the rest of the output keeps the panel's text color.
const ANSI_CLASSES: Readonly<Record<string, string>> = {
  "ansi-red": "text-error-secondary",
  "ansi-bright-red": "text-error-secondary",
  "ansi-green": "text-success-secondary",
  "ansi-bright-green": "text-success-secondary",
  "ansi-yellow": "text-warning-secondary",
  "ansi-bright-yellow": "text-warning-secondary",
  "ansi-blue": "text-brand-primary",
  "ansi-bright-blue": "text-brand-primary",
  "ansi-cyan": "text-brand-primary",
  "ansi-bright-cyan": "text-brand-primary",
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

/** nbformat stores text either whole or as a list of lines. */
function joined(value: unknown): string {
  if (typeof value === "string") return value
  return Array.isArray(value)
    ? value.filter((line) => typeof line === "string").join("")
    : ""
}

function parseOutput(raw: unknown): NotebookOutput | null {
  if (!isObject(raw) || typeof raw.output_type !== "string") return null
  const data = isObject(raw.data)
    ? Object.fromEntries(
        Object.entries(raw.data).map(([type, value]) => [type, joined(value)])
      )
    : {}
  return {
    output_type: raw.output_type,
    text: joined(raw.text),
    data,
    traceback: Array.isArray(raw.traceback)
      ? raw.traceback.filter((line) => typeof line === "string").join("\n")
      : undefined,
  }
}

function parseNotebook(bytes: ArrayBuffer): Notebook {
  let raw: unknown
  try {
    raw = JSON.parse(new TextDecoder().decode(bytes))
  } catch {
    raw = null
  }
  if (!isObject(raw) || !Array.isArray(raw.cells))
    throw new Error("Not a Jupyter notebook.")
  const metadata = isObject(raw.metadata) ? raw.metadata : {}
  const info = isObject(metadata.language_info) ? metadata.language_info : {}
  const kernel = isObject(metadata.kernelspec) ? metadata.kernelspec : {}
  const language =
    typeof info.name === "string"
      ? info.name
      : typeof kernel.language === "string"
        ? kernel.language
        : "python"
  const cells = raw.cells.filter(isObject).map((cell) => ({
    cell_type: typeof cell.cell_type === "string" ? cell.cell_type : "raw",
    source: joined(cell.source),
    execution_count:
      typeof cell.execution_count === "number" ? cell.execution_count : null,
    outputs: Array.isArray(cell.outputs)
      ? cell.outputs
          .map(parseOutput)
          .filter((output): output is NotebookOutput => output !== null)
      : [],
  }))
  return { language, cells }
}

function AnsiText(props: { text: string; className?: string }) {
  return (
    <pre
      className={cn(
        "overflow-x-auto font-mono text-xs leading-5 whitespace-pre-wrap text-primary",
        props.className
      )}
    >
      {Anser.ansiToJson(props.text, {
        use_classes: true,
        remove_empty: true,
      }).map((part, index) => (
        <span
          key={index}
          className={cn(
            ANSI_CLASSES[part.fg],
            part.decorations.includes("bold") && "font-semibold"
          )}
        >
          {part.content}
        </span>
      ))}
    </pre>
  )
}

/** One output, shown as the richest type it carries that the preview can render safely. */
function Output(props: { output: NotebookOutput; theme: "light" | "dark" }) {
  const { output } = props
  if (output.output_type === "stream" && output.text)
    return <AnsiText text={output.text} />
  if (output.output_type === "error" && output.traceback)
    return <AnsiText text={output.traceback} className="text-error-secondary" />
  const image = IMAGE_TYPES.find((type) => output.data[type])
  if (image)
    return (
      <img
        src={`data:${image};base64,${output.data[image]?.replace(/\s/g, "")}`}
        alt="Cell output"
        className="max-w-full"
      />
    )
  const svg = output.data["image/svg+xml"]
  // Through <img> only, which never runs an SVG's scripts.
  if (svg)
    return (
      <img
        src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`}
        alt="Cell output"
        className="max-w-full"
      />
    )
  const html = output.data["text/html"]
  if (html)
    return (
      <SandboxedHtmlFrame
        title="Cell output"
        html={withArtifactShell(html, props.theme)}
        sandbox={ARTIFACT_SANDBOX}
        allow={ARTIFACT_ALLOW}
        csp={ARTIFACT_CSP}
        className="h-64 rounded border border-subtle"
      />
    )
  const markdown = output.data["text/markdown"]
  if (markdown) return <Markdown content={markdown} />
  const text = output.data["text/plain"]
  return text ? <AnsiText text={text} /> : null
}

function Cell(props: {
  cell: NotebookCell
  language: string
  theme: "light" | "dark"
  path: string
  onOpenFile: (path: string) => void
}) {
  const { cell } = props
  let body: ReactNode
  if (cell.cell_type === "markdown")
    body = (
      <Markdown
        content={cell.source}
        workspacePath={props.path}
        onOpenFile={props.onOpenFile}
      />
    )
  else if (cell.cell_type === "code")
    body = (
      <>
        <CodeBlock text={cell.source} language={props.language} />
        {cell.outputs.map((output, index) => (
          <Output key={index} output={output} theme={props.theme} />
        ))}
      </>
    )
  else body = <AnsiText text={cell.source} />
  return (
    <div className="grid grid-cols-[3.5rem_minmax(0,1fr)] gap-space-2">
      <span className="pt-space-1 text-right font-mono text-xxs text-tertiary select-none">
        {cell.cell_type === "code" ? `[${cell.execution_count ?? " "}]` : ""}
      </span>
      <div className="flex min-w-0 flex-col gap-space-2">{body}</div>
    </div>
  )
}

/** A Jupyter notebook: Markdown cells rendered, code highlighted, outputs below each cell. */
export default function NotebookPreview(props: ViewerProps) {
  const theme = useResolvedTheme()
  const parsed = usePreviewData(props.url, parseNotebook)
  const message = queryMessage(parsed)
  if (message || !parsed.data) return message
  const notebook = parsed.data
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-space-4 overflow-auto px-space-4 py-space-4">
      {notebook.cells.map((cell, index) => (
        <Cell
          key={index}
          cell={cell}
          language={notebook.language}
          theme={theme}
          path={props.path}
          onOpenFile={props.onOpenFile}
        />
      ))}
    </div>
  )
}
