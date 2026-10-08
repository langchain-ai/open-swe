import type { SelectedLineRange, SelectionSide } from "@pierre/diffs"

const CONTEXT_LINES = 3

/** Selected diff lines plus surrounding context; `>` marks the selection. */
export interface CodeExcerpt {
  path: string
  /** Side and line range, e.g. "R35-37" or "L12". */
  lineLabel: string
  language: string
  snippet: string
}

export interface ParsedCodeExcerpt {
  location: string
  language: string
  /** Only the selected lines, without context or line numbers. */
  code: string
}

interface FileContents {
  originalContent: string | null
  modifiedContent: string | null
}

function sideExcerpt(
  path: string,
  contents: FileContents,
  side: SelectionSide,
  fromLine: number,
  toLine: number
): CodeExcerpt {
  const source =
    (side === "deletions"
      ? contents.originalContent
      : contents.modifiedContent) ?? ""
  const lines = source.split("\n")
  const start = Math.max(1, Math.min(fromLine, toLine))
  const end = Math.min(lines.length, Math.max(fromLine, toLine))
  const first = Math.max(1, start - CONTEXT_LINES)
  const last = Math.min(lines.length, end + CONTEXT_LINES)
  const width = String(last).length
  const snippet = lines
    .slice(first - 1, last)
    .map((text, i) => {
      const n = first + i
      const marker = n >= start && n <= end ? ">" : " "
      return `${marker} ${String(n).padStart(width)} | ${text}`
    })
    .join("\n")
  const sideLabel = side === "deletions" ? "L" : "R"
  return {
    path,
    lineLabel: `${sideLabel}${start}${start === end ? "" : `-${end}`}`,
    language: path.includes(".") ? (path.split(".").pop() ?? "") : "",
    snippet,
  }
}

// A range dragged from a deletion to an addition spans two files, so each side
// is excerpted separately rather than slicing one file by start..end.
export function selectionExcerpts(
  path: string,
  contents: FileContents,
  range: SelectedLineRange
): Array<CodeExcerpt> {
  const startSide = range.side ?? "additions"
  const endSide = range.endSide ?? startSide
  if (startSide === endSide)
    return [sideExcerpt(path, contents, startSide, range.start, range.end)]
  const deletionLine = startSide === "deletions" ? range.start : range.end
  const additionLine = startSide === "additions" ? range.start : range.end
  return [
    sideExcerpt(path, contents, "deletions", deletionLine, deletionLine),
    sideExcerpt(path, contents, "additions", additionLine, additionLine),
  ]
}

// Excerpts lead the prose as fenced blocks, so the model gets the context while
// the UI parses them back out and shows only the selection.
export function serializeExcerpts(
  text: string,
  excerpts: ReadonlyArray<CodeExcerpt>
): string {
  const blocks = excerpts.map((excerpt) => {
    // Outlast any backtick run in the code so it can't close the fence.
    const fence = "`".repeat(
      Math.max(
        3,
        ...(excerpt.snippet.match(/`+/g) ?? []).map((run) => run.length + 1)
      )
    )
    return `\`${excerpt.path}:${excerpt.lineLabel}\`\n${fence}${excerpt.language}\n${excerpt.snippet}\n${fence}`
  })
  return [...blocks, text.trim()].filter(Boolean).join("\n\n")
}

const EXCERPT_PATTERN =
  /^`([^`\n]+)`\n(`{3,})([\w.-]*)\n([\s\S]*?)\n\2(?:\n+|$)/
const SELECTED_LINE_PATTERN = /^> *\d+ \| ?/

function selectedLines(snippet: string): string {
  const lines = snippet.split("\n")
  const selected = lines.filter((line) => SELECTED_LINE_PATTERN.test(line))
  return selected.length > 0
    ? selected.map((line) => line.replace(SELECTED_LINE_PATTERN, "")).join("\n")
    : snippet
}

export function parseExcerpts(content: string): {
  excerpts: Array<ParsedCodeExcerpt>
  text: string
} {
  const excerpts: Array<ParsedCodeExcerpt> = []
  let rest = content
  let match = EXCERPT_PATTERN.exec(rest)
  while (match) {
    excerpts.push({
      location: match[1] ?? "",
      language: match[3] ?? "",
      code: selectedLines(match[4] ?? ""),
    })
    rest = rest.slice(match[0].length)
    match = EXCERPT_PATTERN.exec(rest)
  }
  return { excerpts, text: rest.trim() }
}
