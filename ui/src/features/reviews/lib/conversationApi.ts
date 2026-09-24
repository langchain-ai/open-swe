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

export type ConversationItem = ConversationComment | ConversationReview

export interface Conversation {
  items: Array<ConversationItem>
}

function conversationPath(owner: string, repo: string, number: number) {
  return `/reviews/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${number}/conversation`
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

async function conversationRequest<T>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
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
  return (await res.json()) as T
}

export function getReviewConversation(
  owner: string,
  repo: string,
  number: number
): Promise<Conversation> {
  return conversationRequest<Conversation>(
    conversationPath(owner, repo, number)
  )
}

export function postReviewConversationComment(
  owner: string,
  repo: string,
  number: number,
  body: string
): Promise<ConversationComment> {
  return conversationRequest<ConversationComment>(
    `${conversationPath(owner, repo, number)}/comments`,
    { method: "POST", body: JSON.stringify({ body }) }
  )
}
