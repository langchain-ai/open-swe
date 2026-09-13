type ComposerInsertListener = (text: string) => void

const listeners = new Set<ComposerInsertListener>()

/** Append text to the mounted chat composer's draft. Returns false when no composer is listening. */
export function insertIntoComposer(text: string): boolean {
  listeners.forEach((listener) => listener(text))
  return listeners.size > 0
}

export function subscribeComposerInsert(
  listener: ComposerInsertListener
): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** Markdown quote of a file excerpt, ready for the user to type a comment under. */
export function quoteFileLines(
  path: string,
  start: number,
  end: number,
  lines: Array<string>,
  language = ""
): string {
  const range = start === end ? `${start}` : `${start}-${end}`
  const body = ["```" + language, ...lines, "```"]
    .map((line) => `> ${line}`)
    .join("\n")
  return `> \`${path}:${range}\`\n${body}\n\n`
}
