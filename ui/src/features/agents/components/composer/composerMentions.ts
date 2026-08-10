/**
 * Finds the file mentions inside a prompt string so the editor can swap each
 * one for a chip. Two spellings are recognised: the markdown link the
 * autocomplete emits (`[app.tsx](ui/src/app.tsx)`) and a bare `@path` a user
 * typed by hand.
 */

import { basenameOfPath } from "./composerTrigger"

export interface ComposerMentionToken {
  readonly path: string
  /** The exact source text, so replacing a chip round-trips to what was typed. */
  readonly source: string
  readonly start: number
  readonly end: number
}

export type ComposerPromptSegment =
  | { type: "text"; text: string }
  | { type: "mention"; path: string; source: string }

// Both require a boundary on each side: a mention is a whole token, never a
// fragment of a longer word or of an inline code span.
const MENTION_TOKEN_REGEX = /(^|\s)@(?:"((?:\\.|[^"\\])*)"|([^\s@"]+))(?=\s|$)/g
const FILE_LINK_TOKEN_REGEX =
  /(^|\s)\[((?:\\.|[^\]\\])*)\]\(([^)\s]+)\)(?=\s|$)/g
const URI_SCHEME_REGEX = /^[A-Za-z][A-Za-z0-9+.-]*:/
const WINDOWS_DRIVE_PATH_REGEX = /^[A-Za-z]:[\\/]/

function collectFileLinkTokens(text: string): Array<ComposerMentionToken> {
  const tokens: Array<ComposerMentionToken> = []

  for (const match of text.matchAll(FILE_LINK_TOKEN_REGEX)) {
    const prefix = match[1] ?? ""
    const label = (match[2] ?? "").replace(/\\(.)/g, "$1")
    const encodedPath = match[3] ?? ""
    let path = encodedPath
    try {
      path = decodeURIComponent(encodedPath)
    } catch {
      // Keep the malformed source rather than dropping a user-authored link.
    }
    const hasExternalScheme =
      URI_SCHEME_REGEX.test(path) && !WINDOWS_DRIVE_PATH_REGEX.test(path)
    // Only links this composer could have written become chips; an ordinary
    // markdown link to a doc or a URL stays plain text.
    if (!path || hasExternalScheme || label !== basenameOfPath(path)) continue

    const start = match.index + prefix.length
    tokens.push({
      path,
      source: text.slice(start, start + match[0].length - prefix.length),
      start,
      end: start + match[0].length - prefix.length,
    })
  }

  return tokens
}

function collectAtTokens(text: string): Array<ComposerMentionToken> {
  const tokens: Array<ComposerMentionToken> = []

  for (const match of text.matchAll(MENTION_TOKEN_REGEX)) {
    const prefix = match[1] ?? ""
    const quotedPath = match[2]
    const path =
      quotedPath !== undefined
        ? quotedPath.replace(/\\(.)/g, "$1")
        : (match[3] ?? "")
    if (!path) continue

    const start = match.index + prefix.length
    tokens.push({
      path,
      source: text.slice(start, start + match[0].length - prefix.length),
      start,
      end: start + match[0].length - prefix.length,
    })
  }

  return tokens
}

export function collectComposerMentions(
  text: string
): Array<ComposerMentionToken> {
  return [...collectFileLinkTokens(text), ...collectAtTokens(text)].sort(
    (left, right) => left.start - right.start
  )
}

/** Splits a prompt into the alternating runs of plain text and mentions the editor renders. */
export function splitPromptIntoSegments(
  prompt: string
): Array<ComposerPromptSegment> {
  if (!prompt) return []

  const segments: Array<ComposerPromptSegment> = []
  let cursor = 0

  for (const token of collectComposerMentions(prompt)) {
    if (token.start < cursor) continue
    if (token.start > cursor)
      segments.push({ type: "text", text: prompt.slice(cursor, token.start) })
    segments.push({ type: "mention", path: token.path, source: token.source })
    cursor = token.end
  }

  if (cursor < prompt.length)
    segments.push({ type: "text", text: prompt.slice(cursor) })
  return segments
}
