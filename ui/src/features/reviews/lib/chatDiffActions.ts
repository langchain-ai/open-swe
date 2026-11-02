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

/** A note the agent left on diff lines for the viewer alone; it never reaches GitHub. */
export interface AgentNote {
  kind: "note"
  id: string
  range: DiffRange
  body: string
}

/** A comment the agent put in someone's pending GitHub review: whoever sent that message. */
export interface AddedComment {
  kind: "added"
  id: string
  range: DiffRange
  login: string
}

export type ChatDiffAction =
  | ProposedComment
  | ProposedReview
  | AgentNote
  | AddedComment

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

const ACTION_TOOLS = new Set([
  "propose_review_comment",
  "propose_pr_review",
  "note_on_diff",
  "add_review_comment",
])

/** Whether a tool's result can carry a page action. */
export function isChatDiffActionTool(name: string): boolean {
  return ACTION_TOOLS.has(name)
}

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
  const id = message.tool_call_id
  const body = typeof result.body === "string" ? result.body : null
  switch (message.name) {
    case "note_on_diff":
      return result.noted === true && body !== null
        ? { kind: "note", id, range, body }
        : null
    case "add_review_comment":
      return result.added === true && typeof result.login === "string"
        ? { kind: "added", id, range, login: result.login }
        : null
    default:
      return result.proposed === true && body !== null
        ? { kind: "comment", id, range, body }
        : null
  }
}

export function rangeLabel(range: DiffRange): string {
  const prefix = range.side === "LEFT" ? "L" : "R"
  return range.startLine === range.endLine
    ? `${prefix}${range.startLine}`
    : `${prefix}${range.startLine}-${range.endLine}`
}
