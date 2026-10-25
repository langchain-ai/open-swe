import { readExcerpt, serializeExcerpts } from "./codeExcerpt"
import type { CodeExcerpt, ParsedCodeExcerpt } from "./codeExcerpt"

/** A passage the user picked out to reply to, and what they said about it. */
export type QuoteReply =
  | { kind: "text"; quote: string; comment: string }
  | { kind: "code"; excerpt: CodeExcerpt; comment: string }

/** Selected text, with the runs of blank lines a selection drags in collapsed. */
export function normalizeQuote(selected: string): string {
  return selected
    .replaceAll("\r\n", "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
}

function blockquote(text: string): string {
  return text
    .split("\n")
    .map((line) => (line ? `> ${line}` : ">"))
    .join("\n")
}

// Each reply leads the message as its passage followed by the comment on it,
// so the model reads every comment against what it is about.
export function serializeReplies(
  text: string,
  replies: ReadonlyArray<QuoteReply>
): string {
  const blocks = replies.map((reply) =>
    reply.kind === "code"
      ? serializeExcerpts(reply.comment, [reply.excerpt])
      : [blockquote(reply.quote), reply.comment.trim()]
          .filter(Boolean)
          .join("\n\n")
  )
  return [...blocks, text.trim()].filter(Boolean).join("\n\n")
}

export type MessageSegment =
  | { kind: "text"; text: string }
  | { kind: "quote"; text: string }
  | { kind: "code"; excerpt: ParsedCodeExcerpt }

const QUOTE_PATTERN = /^((?:>[^\n]*(?:\n|$))+)\n*/
const QUOTE_MARKER = /^> ?/
const PARAGRAPH_BREAK = /\n{2,}/

/** A message's quoted passages, code excerpts and prose, in the order written. */
export function parseMessageSegments(content: string): Array<MessageSegment> {
  const segments: Array<MessageSegment> = []
  let prose: Array<string> = []
  const flushProse = () => {
    const text = prose.join("").trim()
    if (text) segments.push({ kind: "text", text })
    prose = []
  }
  // Quotes and excerpts open a paragraph; anything else is prose up to the next break.
  let rest = content.trim()
  while (rest) {
    const excerpt = readExcerpt(rest)
    const quote = excerpt ? null : QUOTE_PATTERN.exec(rest)
    if (excerpt || quote) {
      flushProse()
      if (excerpt) {
        segments.push({ kind: "code", excerpt: excerpt.excerpt })
        rest = rest.slice(excerpt.length)
      } else if (quote) {
        segments.push({
          kind: "quote",
          text: (quote[1] ?? "")
            .trimEnd()
            .split("\n")
            .map((line) => line.replace(QUOTE_MARKER, ""))
            .join("\n"),
        })
        rest = rest.slice(quote[0].length)
      }
      rest = rest.replace(/^\n+/, "")
      continue
    }
    const paragraphBreak = PARAGRAPH_BREAK.exec(rest)
    const end = paragraphBreak
      ? paragraphBreak.index + paragraphBreak[0].length
      : rest.length
    prose.push(rest.slice(0, end))
    rest = rest.slice(end)
  }
  flushProse()
  return segments
}
