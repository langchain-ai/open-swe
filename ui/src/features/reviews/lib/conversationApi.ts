import {
  dashboardApiUrl,
  dashboardForwardedHeaders,
} from "@/lib/dashboard-fetch"
import type { DiffSide } from "@/features/reviews/lib/chatDiffActions"
import type { PullRequestRef } from "@/features/reviews/lib/reviewKeys"

export type ConversationReviewState =
  | "APPROVED"
  | "CHANGES_REQUESTED"
  | "COMMENTED"
  | "DISMISSED"

export interface ConversationAuthor {
  login: string
  avatar_url: string
  bot: boolean
  /** Set when Open SWE posted through this person's GitHub account; `login` is then Open SWE. */
  posted_by: string | null
}

interface Posted {
  id: number
  author: ConversationAuthor | null
  created_at: string
  body: string
  html_url: string
}

export interface ConversationComment extends Posted {
  kind: "comment"
}

export interface ConversationReview extends Posted {
  kind: "review"
  state: ConversationReviewState
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

export interface ThreadComment extends Posted {
  /** The review this comment was submitted with; a reply is its own review on GitHub. */
  review_id: number | null
}

/** An inline thread: its first comment's anchor, then every reply in order. */
export interface ReviewThread {
  id: number
  node_id: string | null
  path: string
  line: number | null
  start_line: number | null
  side: DiffSide
  original_line: number | null
  diff_hunk: string
  outdated: boolean
  resolved: boolean
  comments: [ThreadComment, ...Array<ThreadComment>]
}

export interface Conversation {
  items: Array<ConversationItem>
  threads: Array<ReviewThread>
}

function reviewPath({ owner, repo, number }: PullRequestRef) {
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

async function send(path: string, init: RequestInit = {}): Promise<Response> {
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

async function read<T>(path: string, init?: RequestInit): Promise<T> {
  return (await (await send(path, init)).json()) as T
}

export function getReviewConversation(
  pr: PullRequestRef
): Promise<Conversation> {
  return read<Conversation>(`${reviewPath(pr)}/conversation`)
}

export function postReviewConversationComment(
  pr: PullRequestRef,
  body: string
): Promise<ConversationComment> {
  return read<ConversationComment>(`${reviewPath(pr)}/conversation/comments`, {
    method: "POST",
    body: JSON.stringify({ body }),
  })
}

export function replyToReviewThread(
  pr: PullRequestRef,
  commentId: number,
  body: string
): Promise<ThreadComment> {
  return read<ThreadComment>(`${reviewPath(pr)}/threads/${commentId}/replies`, {
    method: "POST",
    body: JSON.stringify({ body }),
  })
}

export async function setReviewThreadResolved(
  pr: PullRequestRef,
  threadNodeId: string,
  resolved: boolean
): Promise<void> {
  await send(
    `${reviewPath(pr)}/threads/${encodeURIComponent(threadNodeId)}/resolution`,
    { method: "PUT", body: JSON.stringify({ resolved }) }
  )
}
