/**
 * Trigger parsing for the composer's autocomplete: `@path` file mentions,
 * `/command` slash commands, and `$skill` skill commands. The prompt stays a
 * plain string end to end — the Lexical editor only renders chips over it — so
 * everything here operates on
 * text plus a cursor offset rather than on editor nodes.
 */

export type ComposerTriggerKind = "path" | "slash-command" | "skill-command"

/** Slash commands open-swe understands. `model` opens the picker rather than editing the prompt. */
export type ComposerSlashCommand = "plan" | "default" | "model"

export interface ComposerTrigger {
  kind: ComposerTriggerKind
  query: string
  rangeStart: number
  rangeEnd: number
}

/**
 * Drag payload for dropping a repo path onto the composer. A private type, not
 * `text/plain`, so dragging arbitrary prose in never becomes a file mention.
 */
export const COMPOSER_PATH_DRAG_MIME = "application/x-open-swe-path"

const SIMPLE_MENTION_PATH_REGEX = /^[^\s@"\\]+$/

export function serializeComposerMentionPath(path: string): string {
  if (SIMPLE_MENTION_PATH_REGEX.test(path)) return path
  return `"${path.replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`
}

export function basenameOfPath(path: string): string {
  const separatorIndex = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"))
  return separatorIndex >= 0 ? path.slice(separatorIndex + 1) : path
}

function escapeMarkdownLinkLabel(label: string): string {
  return label
    .replaceAll("\\", "\\\\")
    .replaceAll("[", "\\[")
    .replaceAll("]", "\\]")
}

function encodeMarkdownLinkDestination(path: string): string {
  return encodeURI(path)
    .replaceAll("(", "%28")
    .replaceAll(")", "%29")
    .replaceAll("#", "%23")
    .replaceAll("?", "%3F")
    .replaceAll("\\", "%5C")
}

/**
 * Mentions serialize as markdown links so the agent receives an unambiguous
 * path even though the composer displays a compact chip.
 */
export function serializeComposerFileLink(path: string): string {
  return `[${escapeMarkdownLinkLabel(basenameOfPath(path))}](${encodeMarkdownLinkDestination(path)})`
}

function clampCursor(text: string, cursor: number): number {
  if (!Number.isFinite(cursor)) return text.length
  return Math.max(0, Math.min(text.length, Math.floor(cursor)))
}

function isWhitespace(char: string): boolean {
  return char === " " || char === "\n" || char === "\t" || char === "\r"
}

export function detectComposerTrigger(
  text: string,
  cursorInput: number
): ComposerTrigger | null {
  const cursor = clampCursor(text, cursorInput)
  let tokenIdx = cursor - 1
  while (tokenIdx >= 0 && !isWhitespace(text[tokenIdx] ?? "")) tokenIdx -= 1
  const tokenStart = tokenIdx + 1
  const token = text.slice(tokenStart, cursor)
  if (
    !token.startsWith("@") &&
    !token.startsWith("/") &&
    !token.startsWith("$")
  )
    return null

  return {
    kind: token.startsWith("@")
      ? "path"
      : token.startsWith("$")
        ? "skill-command"
        : "slash-command",
    query: token.slice(1),
    rangeStart: tokenStart,
    rangeEnd: cursor,
  }
}

export function replaceTextRange(
  text: string,
  rangeStart: number,
  rangeEnd: number,
  replacement: string
): { text: string; cursor: number } {
  const safeStart = Math.max(0, Math.min(text.length, rangeStart))
  const safeEnd = Math.max(safeStart, Math.min(text.length, rangeEnd))
  return {
    text: `${text.slice(0, safeStart)}${replacement}${text.slice(safeEnd)}`,
    cursor: safeStart + replacement.length,
  }
}
