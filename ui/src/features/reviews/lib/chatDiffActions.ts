export type DiffSide = "LEFT" | "RIGHT"

export interface DiffRange {
  file: string
  startLine: number
  endLine: number
  side: DiffSide
}

export interface ProposedComment {
  kind: "comment"
  id: string
  range: DiffRange
  body: string
}

export type ReviewEvent = "APPROVE" | "REQUEST_CHANGES" | "COMMENT"

export interface ProposedReview {
  kind: "review"
  id: string
  event: ReviewEvent
  body: string
}

export type ChatDiffAction = ProposedComment | ProposedReview

const REVIEW_EVENTS: ReadonlyArray<ReviewEvent> = [
  "APPROVE",
  "REQUEST_CHANGES",
  "COMMENT",
]

/** The fields of a LangGraph tool message this module reads. */
export interface ToolMessageLike {
  type?: string
  name?: string
  tool_call_id?: string
  content: unknown
}

function parseRange(value: unknown): DiffRange | null {
  if (typeof value !== "object" || value === null) return null
  const raw = value as Record<string, unknown>
  const { file, start_line: start, end_line: end, side } = raw
  if (typeof file !== "string" || !file) return null
  if (typeof start !== "number" || typeof end !== "number") return null
  if (side !== "LEFT" && side !== "RIGHT") return null
  return { file, startLine: start, endLine: end, side }
}

function contentObject(content: unknown): Record<string, unknown> | null {
  const text =
    typeof content === "string"
      ? content
      : Array.isArray(content)
        ? content
            .map((block: unknown) =>
              typeof block === "string"
                ? block
                : typeof block === "object" &&
                    block !== null &&
                    "text" in block &&
                    typeof block.text === "string"
                  ? block.text
                  : ""
            )
            .join("")
        : ""
  try {
    const parsed: unknown = JSON.parse(text)
    return typeof parsed === "object" && parsed !== null
      ? (parsed as Record<string, unknown>)
      : null
  } catch (error) {
    console.warn("Could not parse a review chat tool result", error)
    return null
  }
}

const ACTION_TOOLS = new Set(["propose_review_comment", "propose_pr_review"])

/** The page action a successful chat tool result carries. */
export function chatDiffAction(
  message: ToolMessageLike
): ChatDiffAction | null {
  if (message.type !== "tool" || !message.tool_call_id) return null
  if (!message.name || !ACTION_TOOLS.has(message.name)) return null
  const result = contentObject(message.content)
  if (!result) return null
  if (message.name === "propose_pr_review") {
    const event = REVIEW_EVENTS.find((value) => value === result.event)
    return result.proposed === true && event
      ? {
          kind: "review",
          id: message.tool_call_id,
          event,
          body: typeof result.body === "string" ? result.body : "",
        }
      : null
  }
  const range = parseRange(result.range)
  if (!range) return null
  return result.proposed === true && typeof result.body === "string"
    ? { kind: "comment", id: message.tool_call_id, range, body: result.body }
    : null
}

export function rangeLabel(range: DiffRange): string {
  const prefix = range.side === "LEFT" ? "L" : "R"
  return range.startLine === range.endLine
    ? `${prefix}${range.startLine}`
    : `${prefix}${range.startLine}-${range.endLine}`
}
