import {
  dashboardApiUrl,
  dashboardForwardedHeaders,
} from "@/lib/dashboard-fetch"

export type ConversationReviewState =
  | "APPROVED"
  | "CHANGES_REQUESTED"
  | "COMMENTED"
  | "DISMISSED"

export interface ConversationAuthor {
  login: string
  avatar_url: string
  bot: boolean
}

interface ConversationItemBase {
  id: number
  author: ConversationAuthor | null
  created_at: string
  body: string
  html_url: string
}

export interface ConversationComment extends ConversationItemBase {
  kind: "comment"
}

export interface ConversationReview extends ConversationItemBase {
  kind: "review"
  state: ConversationReviewState
  inline_comment_count: number
}

export interface ConversationCommit {
  kind: "commit"
  sha: string
  author: ConversationAuthor | null
  created_at: string
  message: string
  html_url: string
}

export type ConversationItem =
  | ConversationComment
  | ConversationReview
  | ConversationCommit

export interface ThreadComment {
  id: number
  author: ConversationAuthor | null
  created_at: string
  body: string
  html_url: string
}

/** An inline thread: its first comment's anchor, then every reply in order. */
export interface ReviewThread {
  id: number
  node_id: string | null
  review_id: number | null
  path: string
  line: number | null
  start_line: number | null
  side: "LEFT" | "RIGHT"
  original_line: number | null
  diff_hunk: string
  outdated: boolean
  resolved: boolean
  comments: Array<ThreadComment>
}

export interface Conversation {
  items: Array<ConversationItem>
  threads: Array<ReviewThread>
}

export function reviewConversationQueryKey(
  owner: string,
  repo: string,
  number: number
): ReadonlyArray<string | number> {
  return ["review-conversation", owner, repo, number]
}

function reviewPath(owner: string, repo: string, number: number) {
  return `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}`
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json()
    if (typeof body === "object" && body !== null && "detail" in body) {
      const { detail } = body
      return typeof detail === "string" ? detail : JSON.stringify(detail)
    }
  } catch (error) {
    console.warn("conversation error body was not JSON", error)
  }
  return res.statusText || `Request failed (${res.status})`
}

async function conversationRequest(
  path: string,
  init: RequestInit = {}
): Promise<Response> {
  const res = await fetch(dashboardApiUrl(path), {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...dashboardForwardedHeaders(),
      ...init.headers,
    },
  })
  if (!res.ok) throw new Error(await errorDetail(res))
  return res
}

export async function getReviewConversation(
  owner: string,
  repo: string,
  number: number
): Promise<Conversation> {
  const res = await conversationRequest(
    `${reviewPath(owner, repo, number)}/conversation`
  )
  return (await res.json()) as Conversation
}

export async function postReviewConversationComment(
  owner: string,
  repo: string,
  number: number,
  body: string
): Promise<ConversationComment> {
  const res = await conversationRequest(
    `${reviewPath(owner, repo, number)}/conversation/comments`,
    { method: "POST", body: JSON.stringify({ body }) }
  )
  return (await res.json()) as ConversationComment
}

export async function replyToReviewThread(
  owner: string,
  repo: string,
  number: number,
  commentId: number,
  body: string
): Promise<ThreadComment> {
  const res = await conversationRequest(
    `${reviewPath(owner, repo, number)}/threads/${commentId}/replies`,
    { method: "POST", body: JSON.stringify({ body }) }
  )
  return (await res.json()) as ThreadComment
}

export async function setReviewThreadResolved(
  owner: string,
  repo: string,
  number: number,
  threadNodeId: string,
  resolved: boolean
): Promise<void> {
  await conversationRequest(
    `${reviewPath(owner, repo, number)}/threads/${encodeURIComponent(threadNodeId)}/resolution`,
    { method: "PUT", body: JSON.stringify({ resolved }) }
  )
}
