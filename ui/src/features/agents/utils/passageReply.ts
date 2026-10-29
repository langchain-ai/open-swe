/** Where a passage someone replied to came from. */
export type PassageSource = "conversation" | "terminal" | "file"

/**
 * A passage someone selected, the text around it, and what they said about it.
 * `before`, `selection` and `after` are contiguous in the source, so the agent
 * can tell exactly which part was selected.
 */
export interface PassageReply {
  source: PassageSource
  /** For a file: its path and selected lines, such as "6" or "4-6". */
  file?: string
  lines?: string
  before: string
  selection: string
  after: string
  comment: string
}

// Under the server's limits (PassageReply in openswe/utils/dashboard_handoff.py),
// so an oversized passage is trimmed here rather than rejecting the whole send.
const CONTEXT_LIMIT = 1_000
export const SELECTION_LIMIT = 20_000

/** Selected text, with the runs of blank lines a selection drags in collapsed. */
export function normalizeSelection(selected: string): string {
  return selected
    .replaceAll("\r\n", "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
    .slice(0, SELECTION_LIMIT)
}

/**
 * `text[start, end)` as the selection, with the rest of its lines and one whole
 * line on either side as context. Whitespace at the selection's edges moves into
 * the context so the selection is exactly what was meant.
 */
export function passageAround(
  text: string,
  start: number,
  end: number
): Pick<PassageReply, "before" | "selection" | "after"> {
  const raw = text.slice(start, end)
  const leading = raw.length - raw.trimStart().length
  const trailing = raw.length - raw.trimEnd().length
  const from = start + leading
  const to = Math.max(from, end - trailing)
  const lineStart = text.lastIndexOf("\n", from - 1) + 1
  const before = lineStart > 0 ? text.lastIndexOf("\n", lineStart - 2) + 1 : 0
  const lineEnd = text.indexOf("\n", to)
  const nextEnd = lineEnd === -1 ? -1 : text.indexOf("\n", lineEnd + 1)
  // Context is best effort: keep the part nearest the selection.
  return {
    before: text.slice(Math.max(before, from - CONTEXT_LIMIT), from),
    selection: normalizeSelection(text.slice(from, to)),
    after: text.slice(
      to,
      Math.min(nextEnd === -1 ? text.length : nextEnd, to + CONTEXT_LIMIT)
    ),
  }
}
