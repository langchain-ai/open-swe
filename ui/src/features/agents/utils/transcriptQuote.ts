/** Text picked out of the transcript; collapses runs of blank lines a selection drags in. */
export function normalizeQuote(selected: string): string {
  return selected
    .replaceAll("\r\n", "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
}

// Quotes lead the prose as Markdown blockquotes, so the model reads them as
// quoted while the UI parses them back out and shows them as quotes.
export function serializeQuotes(
  text: string,
  quotes: ReadonlyArray<string>
): string {
  const blocks = quotes.map((quote) =>
    quote
      .split("\n")
      .map((line) => (line ? `> ${line}` : ">"))
      .join("\n")
  )
  return [...blocks, text.trim()].filter(Boolean).join("\n\n")
}

const QUOTE_PATTERN = /^((?:>[^\n]*(?:\n|$))+)\n*/
const QUOTE_MARKER = /^> ?/

export function parseQuotes(content: string): {
  quotes: Array<string>
  text: string
} {
  const quotes: Array<string> = []
  let rest = content
  let match = QUOTE_PATTERN.exec(rest)
  while (match) {
    quotes.push(
      (match[1] ?? "")
        .trimEnd()
        .split("\n")
        .map((line) => line.replace(QUOTE_MARKER, ""))
        .join("\n")
    )
    rest = rest.slice(match[0].length)
    match = QUOTE_PATTERN.exec(rest)
  }
  return { quotes, text: rest.trim() }
}
