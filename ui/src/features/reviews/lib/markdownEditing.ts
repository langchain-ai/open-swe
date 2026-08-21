/**
 * The markdown toolbar behind the review page's comment boxes: what each action
 * does to a textarea's value + selection, and how the buttons are grouped.
 * Pure text in, text out — the caller owns the textarea.
 */

import {
  CodeIcon,
  LinkIcon,
  ListBulletsIcon,
  ListChecksIcon,
  ListNumbersIcon,
  QuotesIcon,
  TextBIcon,
  TextHIcon,
  TextItalicIcon,
} from "@phosphor-icons/react"

import type { Icon } from "@phosphor-icons/react"

export type MarkdownAction =
  | "heading"
  | "bold"
  | "italic"
  | "quote"
  | "code"
  | "link"
  | "ul"
  | "ol"
  | "task"

export interface EditState {
  value: string
  start: number
  end: number
}

/**
 * Wrap the current selection (or a placeholder when empty) with a marker, e.g.
 * **bold**. Returns the new value and the selection to restore.
 */
export function wrapSelection(
  state: EditState,
  marker: string,
  placeholder: string
): EditState {
  const selected = state.value.slice(state.start, state.end) || placeholder
  const value =
    state.value.slice(0, state.start) +
    marker +
    selected +
    marker +
    state.value.slice(state.end)
  const start = state.start + marker.length
  return { value, start, end: start + selected.length }
}

/**
 * Prefix each line touched by the selection, e.g. "> " for quotes or "1. " for
 * ordered lists (prefix is computed per line so numbering increments).
 */
export function prefixLines(
  state: EditState,
  prefix: (index: number) => string
): EditState {
  const lineStart = state.value.lastIndexOf("\n", state.start - 1) + 1
  const block = state.value.slice(lineStart, state.end)
  const prefixed = block
    .split("\n")
    .map((line, index) => prefix(index) + line)
    .join("\n")
  const value =
    state.value.slice(0, lineStart) + prefixed + state.value.slice(state.end)
  return { value, start: lineStart, end: lineStart + prefixed.length }
}

export function applyMarkdownAction(
  state: EditState,
  action: MarkdownAction
): EditState {
  switch (action) {
    case "bold":
      return wrapSelection(state, "**", "bold text")
    case "italic":
      return wrapSelection(state, "_", "italic text")
    case "code":
      return wrapSelection(state, "`", "code")
    case "heading":
      return prefixLines(state, () => "### ")
    case "quote":
      return prefixLines(state, () => "> ")
    case "ul":
      return prefixLines(state, () => "- ")
    case "ol":
      return prefixLines(state, (index) => `${index + 1}. `)
    case "task":
      return prefixLines(state, () => "- [ ] ")
    case "link": {
      const text = state.value.slice(state.start, state.end) || "text"
      const inserted = `[${text}](url)`
      const value =
        state.value.slice(0, state.start) +
        inserted +
        state.value.slice(state.end)
      const urlStart = state.start + text.length + 3
      return { value, start: urlStart, end: urlStart + 3 }
    }
  }
}

export interface ToolbarItem {
  action: MarkdownAction
  label: string
  Icon: Icon
}

/** Grouped to match GitHub's comment toolbar (format group, then list group). */
export const MARKDOWN_TOOLBAR: ReadonlyArray<ReadonlyArray<ToolbarItem>> = [
  [
    { action: "heading", label: "Heading", Icon: TextHIcon },
    { action: "bold", label: "Bold", Icon: TextBIcon },
    { action: "italic", label: "Italic", Icon: TextItalicIcon },
    { action: "quote", label: "Quote", Icon: QuotesIcon },
    { action: "code", label: "Code", Icon: CodeIcon },
    { action: "link", label: "Link", Icon: LinkIcon },
  ],
  [
    { action: "ul", label: "Bulleted list", Icon: ListBulletsIcon },
    { action: "ol", label: "Numbered list", Icon: ListNumbersIcon },
    { action: "task", label: "Task list", Icon: ListChecksIcon },
  ],
]
