const COLLAPSE_CHAR_THRESHOLD = 1_200
const COLLAPSE_LINE_THRESHOLD = 24
const LEADING_CONTEXT_CHAR_LIMIT = 240

interface TextSegment {
  type: "text"
  text: string
}

interface PastedSegment {
  type: "pasted"
  text: string
  label: string
}

type CollapsedUserTextSegment = TextSegment | PastedSegment

function lineCount(text: string): number {
  if (!text) return 0
  return text.split(/\r\n|\r|\n/).length
}

function shouldCollapse(text: string): boolean {
  return (
    text.length >= COLLAPSE_CHAR_THRESHOLD ||
    lineCount(text) >= COLLAPSE_LINE_THRESHOLD
  )
}

function compactNumber(value: number): string {
  if (value < 1_000) return String(value)
  const rounded = Math.round(value / 100) / 10
  return `${Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1)}k`
}

function pastedLabel(text: string): string {
  const lines = lineCount(text)
  if (lines >= COLLAPSE_LINE_THRESHOLD) {
    return `[Pasted ${compactNumber(lines)} lines]`
  }
  return `[Pasted ${compactNumber(text.length)} chars]`
}

function leadingContextLength(text: string): number {
  const paragraphBreak = /\n\s*\n/.exec(text)
  if (
    paragraphBreak?.index &&
    paragraphBreak.index <= LEADING_CONTEXT_CHAR_LIMIT
  ) {
    return paragraphBreak.index + paragraphBreak[0].length
  }

  const firstNewline = text.indexOf("\n")
  if (firstNewline > 0 && firstNewline <= LEADING_CONTEXT_CHAR_LIMIT) {
    return firstNewline + 1
  }

  return 0
}

export function collapsedUserTextSegments(
  text: string
): Array<CollapsedUserTextSegment> {
  if (!shouldCollapse(text)) return [{ type: "text", text }]

  const prefixLength = leadingContextLength(text)
  const prefix = text.slice(0, prefixLength)
  const pasted = text.slice(prefixLength).trim()
  if (!pasted) return [{ type: "text", text }]

  return [
    ...(prefix ? [{ type: "text" as const, text: prefix }] : []),
    { type: "pasted" as const, text: pasted, label: pastedLabel(pasted) },
  ]
}
