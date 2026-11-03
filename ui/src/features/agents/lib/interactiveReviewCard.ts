/** What an interactive review showed in the dashboard: a chunk with its "Next", or its close. */
export interface InteractiveReviewCard {
  text: string
  /** The chunk the server approves when the reader clicks "Next"; empty on a closing card. */
  nextTs: string
  /** Buttons that answer with their own label, as a Slack option does. */
  options: Array<string>
}

const CARD_TOOLS = new Set([
  "walkthrough_show_chunk",
  "walkthrough_show_lines",
  "walkthrough_show_other",
  "walkthrough_end",
])

/** Whether a tool call showed a card, judged from as little as the transcript's preview. */
export function showsInteractiveReviewCard(
  toolName: string | undefined,
  output: string | undefined
): boolean {
  return !!toolName && CARD_TOOLS.has(toolName) && !!output?.includes('"card":')
}

export function parseInteractiveReviewCard(
  output: string | undefined
): InteractiveReviewCard | null {
  if (!output) return null
  let value: unknown
  try {
    value = JSON.parse(output)
  } catch {
    return null
  }
  if (typeof value !== "object" || value === null) return null
  const card = (value as { card?: unknown }).card
  if (typeof card !== "object" || card === null) return null
  const { text, next_ts, options } = card as Record<string, unknown>
  if (typeof text !== "string") return null
  return {
    text,
    nextTs: typeof next_ts === "string" ? next_ts : "",
    options: Array.isArray(options)
      ? options.filter((option): option is string => typeof option === "string")
      : [],
  }
}
