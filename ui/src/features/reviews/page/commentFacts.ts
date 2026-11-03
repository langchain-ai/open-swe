import type { ConversationItem } from "@/features/reviews/lib/conversationApi"

export interface CommentFact {
  key: string
  /** Markdown, the row's other cells joined. */
  value: string
}

const BOLD_KEY = /^(?:\*\*|__)(.+?)(?:\*\*|__)$/
const SEPARATOR = /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$/

function cells(line: string): Array<string> {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/(?<!\\)\|$/, "")
    .split(/(?<!\\)\|/)
    .map((cell) => cell.trim().replaceAll("\\|", "|"))
}

/** Rows of a Markdown table whose first cell is bold, read as `key: value`. */
function tableFacts(body: string): Array<CommentFact> {
  const facts: Array<CommentFact> = []
  let inTable = false
  let fenced = false
  const lines = body.split(/\r?\n/)
  lines.forEach((line, index) => {
    const text = line.trim()
    if (text.startsWith("```") || text.startsWith("~~~")) fenced = !fenced
    if (fenced || !text.startsWith("|")) {
      inTable = false
      return
    }
    if (!inTable) {
      inTable = SEPARATOR.test(lines[index + 1]?.trim() ?? "")
      return
    }
    if (SEPARATOR.test(text)) return
    const [first = "", ...rest] = cells(text)
    const key = BOLD_KEY.exec(first)?.[1]?.trim()
    const value = rest.filter(Boolean).join(" · ")
    if (key && value) facts.push({ key, value })
  })
  return facts
}

/** Key-value table rows across the PR's comments; a later comment overwrites an earlier one's key. */
export function commentFacts(
  items: ReadonlyArray<ConversationItem>
): Array<CommentFact> {
  const facts = new Map<string, string>()
  items
    .flatMap((item) => (item.kind === "commit" ? [] : [item]))
    .toSorted((a, b) => a.created_at.localeCompare(b.created_at))
    .forEach((item) =>
      tableFacts(item.body).forEach(({ key, value }) => facts.set(key, value))
    )
  return Array.from(facts, ([key, value]) => ({ key, value }))
}
