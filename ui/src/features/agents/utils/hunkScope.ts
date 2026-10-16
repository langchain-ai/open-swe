import type { ChangeContent, FileDiffMetadata, Hunk } from "@pierre/diffs"

const DECLARATIONS: ReadonlyArray<RegExp> = [
  /^(export\s+)?(default\s+)?(declare\s+)?(abstract\s+)?(async\s+)?(function\*?|class|interface|enum|namespace|module|type)\s+[\w$]/,
  /^(export\s+)?(const|let|var)\s+[\w$]+\s*(:[^=]+)?=\s*([\w$.]+(<[^>]*>)?\(\s*)?(async\s+)?(function\b|\(|[\w$]+\s*=>)/,
  /^(async\s+)?def\s+\w/,
  /^func\s/,
  /^(pub(\([^)]*\))?\s+)?(const\s+)?(async\s+)?(unsafe\s+)?(fn|impl|struct|enum|trait|mod)\b/,
  /^((public|private|protected|internal|static|final|abstract|override|open|suspend|inline|sealed|data)\s+)*(fun|func|object)\s/,
  /^((public|private|protected|static|final|abstract|synchronized|virtual|override|async)\s+)+[\w<>[\],.? ]+\s+\w+\s*\(/,
  /^((public|private|protected|static|readonly|async|override|get|set)\s+)*\*?[\w$#]+\s*(<[^>]*>)?\s*\([^)]*\)\s*(:\s*[^{]+)?\{$/,
  /^(describe|it|test|context)(\.\w+)?\(/,
]

const CONTROL_FLOW =
  /^(if|else|for|foreach|while|do|switch|case|catch|try|with|return|await|yield|throw|new)\b/

const CLOSER = /^([)\]}]|end\b)/

function isMarkdown(name: string): boolean {
  return /\.(md|mdx|markdown)$/i.test(name)
}

function indentOf(line: string): number {
  return line.length - line.trimStart().length
}

function isDeclaration(trimmed: string): boolean {
  return (
    !CONTROL_FLOW.test(trimmed) &&
    DECLARATIONS.some((pattern) => pattern.test(trimmed))
  )
}

function anchorIndent(lines: Array<string>, anchor: number): number | null {
  for (let i = anchor; i < lines.length; i++) {
    const line = lines[i] ?? ""
    const trimmed = line.trim()
    if (!trimmed) continue
    return indentOf(line) + (CLOSER.test(trimmed) ? 1 : 0)
  }
  return null
}

function nearestHeading(lines: Array<string>, anchor: number): string | null {
  for (let i = anchor - 1; i >= 0; i--) {
    const line = lines[i] ?? ""
    if (/^#{1,6}\s/.test(line)) return line.trim()
  }
  return null
}

/** The declaration line of the innermost block that encloses `lines[anchor]`. */
export function enclosingScope(
  lines: Array<string>,
  anchor: number,
  fileName: string
): string | null {
  if (isMarkdown(fileName)) return nearestHeading(lines, anchor)
  let threshold = anchorIndent(lines, anchor)
  if (threshold == null) return null
  for (let i = anchor - 1; i >= 0 && threshold > 0; i--) {
    const line = lines[i] ?? ""
    const trimmed = line.trim()
    if (!trimmed) continue
    const indent = indentOf(line)
    if (indent >= threshold || CLOSER.test(trimmed)) continue
    if (isDeclaration(trimmed)) return trimmed
    threshold = indent
  }
  return null
}

function hunkScope(fileDiff: FileDiffMetadata, hunk: Hunk): string | null {
  const change = hunk.hunkContent.find(
    (content): content is ChangeContent => content.type === "change"
  )
  if (change == null) return null
  return change.additions > 0
    ? enclosingScope(
        fileDiff.additionLines,
        change.additionLineIndex,
        fileDiff.name
      )
    : enclosingScope(
        fileDiff.deletionLines,
        change.deletionLineIndex,
        fileDiff.prevName ?? fileDiff.name
      )
}

/**
 * Fills `hunkContext` for diffs built from full file contents, which carry no
 * `@@ … @@ scope` text; patch-sourced hunks keep git's.
 */
export function withHunkScopes(fileDiff: FileDiffMetadata): FileDiffMetadata {
  if (fileDiff.isPartial) return fileDiff
  return {
    ...fileDiff,
    hunks: fileDiff.hunks.map((hunk) => {
      if (hunk.hunkContext) return hunk
      const scope = hunkScope(fileDiff, hunk)
      return scope ? { ...hunk, hunkContext: scope } : hunk
    }),
  }
}
