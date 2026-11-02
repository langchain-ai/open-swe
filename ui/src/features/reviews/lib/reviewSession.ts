/**
 * A review session: line comments for Open SWE collected on one pull request's
 * review page, unsent until the reviewer finishes and hands them to the PR's
 * review chat as one message. Drafts persist per browser, so a reload or a
 * trip to another page never loses them.
 */
import type { SelectedLineRange } from "@pierre/diffs"
import { create } from "zustand"
import { createJSONStorage, persist } from "zustand/middleware"

import type { FollowUpBehavior } from "@/lib/api"
import {
  serializeExcerpts,
  type CodeExcerpt,
} from "@/features/agents/utils/codeExcerpt"
import { commentRangeLabel } from "@/features/reviews/lib/lineRange"
import type { PullRequestRef } from "@/features/reviews/lib/reviewKeys"

/** One unsent comment for Open SWE, anchored where it was written. */
export interface AgentComment {
  id: string
  path: string
  range: SelectedLineRange
  /** The lines it was written on, with context, as they read then. */
  excerpts: ReadonlyArray<CodeExcerpt>
  /** The pull request head the comment was written against. */
  headSha: string | null
  body: string
  createdAt: number
}

/**
 * Where a batch is on its way to the agent. `sending` hasn't reached the chat
 * yet; `sent` is on its way; `scheduled` waits behind the current run;
 * `delivered` is in the conversation.
 */
export type HandoffStatus = "sending" | "sent" | "scheduled" | "delivered"

/** The comments handed to the chat as one message, kept until it lands so nothing is lost on the way. */
export interface Handoff {
  messageId: string
  text: string
  /** While a run is live: wait for it (`queue`), or redirect it (`steer`). */
  delivery: FollowUpBehavior
  comments: ReadonlyArray<AgentComment>
  status: HandoffStatus
  /** The reviewer asked for a scheduled batch back. */
  withdrawing: boolean
}

export interface ReviewSession {
  active: boolean
  comments: ReadonlyArray<AgentComment>
  handoff: Handoff | null
  /** Why the last handoff came back, shown until the reviewer acts. */
  error: string | null
}

/** What the chat shows of a handed-off message. */
export type Sighting =
  | { kind: "sending" }
  | { kind: "queued" }
  | { kind: "delivered"; running: boolean }
  | { kind: "failed"; error: string }
  /** Nowhere in the chat; `settled` once it has loaded and this page didn't just send it. */
  | { kind: "missing"; settled: boolean; running: boolean }

/** What a sighting did to the session, for the page to tell the reviewer. */
export type HandoffOutcome = "lost" | "failed" | "finished" | null

interface ReviewSessionState {
  sessions: Record<string, ReviewSession>
  start: (key: string) => void
  /** Ends the session and drops its unsent drafts; a batch already handed off is untouched. */
  abandon: (key: string) => void
  add: (key: string, comment: AgentComment) => void
  edit: (key: string, id: string, body: string) => void
  /** The lines a draft is about, once they load; a draft is saved before they do. */
  attachExcerpts: (
    key: string,
    id: string,
    excerpts: ReadonlyArray<CodeExcerpt>
  ) => void
  remove: (key: string, id: string) => void
  /** Hands every written draft to the chat as one message; the session ends while it's on its way, unless blank drafts stay behind. */
  send: (
    key: string,
    delivery: FollowUpBehavior,
    headSha: string | null
  ) => void
  /** The chat took the message and is starting it. */
  dispatched: (key: string, messageId: string) => void
  /** Asks the chat to withdraw a scheduled batch. */
  withdraw: (key: string) => void
  /** The withdrawal failed; the batch stays scheduled. */
  keepScheduled: (key: string, messageId: string) => void
  /** Puts the batch's comments back as drafts, ahead of any written since. */
  restore: (key: string, messageId: string, error: string | null) => void
  /** Moves a handoff along by what the chat shows of its message. */
  observe: (key: string, messageId: string, seen: Sighting) => HandoffOutcome
  clearError: (key: string) => void
}

const STORAGE_KEY = "open-swe:review-sessions"
const STORAGE_VERSION = 1

export function reviewSessionKey(pr: PullRequestRef): string {
  return `${pr.owner}/${pr.repo}#${pr.number}`
}

const idle: ReviewSession = {
  active: false,
  comments: [],
  handoff: null,
  error: null,
}

/** Reads persisted sessions, which are untrusted input: anything unrecognized is dropped. */
class ReviewSessionStorage {
  static parse(persisted: unknown): Record<string, ReviewSession> {
    const raw =
      persisted && typeof persisted === "object" && "sessions" in persisted
        ? persisted.sessions
        : null
    if (!raw || typeof raw !== "object") return {}
    const sessions: Record<string, ReviewSession> = {}
    for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
      const session = ReviewSessionStorage.session(value)
      if (session) sessions[key] = session
    }
    return sessions
  }

  static session(value: unknown): ReviewSession | null {
    if (!value || typeof value !== "object") return null
    const record = value as Record<string, unknown>
    return {
      active: record.active === true,
      comments: ReviewSessionStorage.comments(record.comments),
      handoff: ReviewSessionStorage.handoff(record.handoff),
      error: typeof record.error === "string" ? record.error : null,
    }
  }

  static handoff(value: unknown): Handoff | null {
    if (!value || typeof value !== "object") return null
    const record = value as Record<string, unknown>
    const statuses: ReadonlyArray<unknown> = [
      "sending",
      "sent",
      "scheduled",
      "delivered",
    ]
    if (
      typeof record.messageId !== "string" ||
      typeof record.text !== "string" ||
      (record.delivery !== "queue" && record.delivery !== "steer") ||
      !statuses.includes(record.status)
    )
      return null
    return {
      messageId: record.messageId,
      text: record.text,
      delivery: record.delivery,
      comments: ReviewSessionStorage.comments(record.comments),
      status: record.status as HandoffStatus,
      withdrawing: record.withdrawing === true,
    }
  }

  static comments(value: unknown): Array<AgentComment> {
    if (!Array.isArray(value)) return []
    return value.flatMap((item: unknown): Array<AgentComment> => {
      if (!item || typeof item !== "object") return []
      const record = item as Record<string, unknown>
      const range = record.range as Record<string, unknown> | undefined
      if (
        typeof record.id !== "string" ||
        typeof record.path !== "string" ||
        typeof record.body !== "string" ||
        !range ||
        typeof range.start !== "number" ||
        typeof range.end !== "number"
      )
        return []
      return [
        {
          id: record.id,
          path: record.path,
          range: record.range as SelectedLineRange,
          excerpts: Array.isArray(record.excerpts)
            ? record.excerpts.filter(
                (excerpt: unknown): excerpt is CodeExcerpt =>
                  !!excerpt &&
                  typeof excerpt === "object" &&
                  typeof (excerpt as CodeExcerpt).path === "string" &&
                  typeof (excerpt as CodeExcerpt).lineLabel === "string" &&
                  typeof (excerpt as CodeExcerpt).language === "string" &&
                  typeof (excerpt as CodeExcerpt).snippet === "string"
              )
            : [],
          headSha: typeof record.headSha === "string" ? record.headSha : null,
          body: record.body,
          createdAt:
            typeof record.createdAt === "number" ? record.createdAt : 0,
        },
      ]
    })
  }
}

export const useReviewSessions = create<ReviewSessionState>()(
  persist(
    (set, get) => {
      const read = (key: string): ReviewSession => get().sessions[key] ?? idle
      const write = (key: string, next: ReviewSession) => {
        const sessions = { ...get().sessions }
        if (
          !next.active &&
          !next.handoff &&
          next.comments.length === 0 &&
          !next.error
        )
          delete sessions[key]
        else sessions[key] = next
        set({ sessions })
      }
      const update = (key: string, change: Partial<ReviewSession>) =>
        write(key, { ...read(key), ...change })
      const updateHandoff = (
        key: string,
        messageId: string,
        change: Partial<Handoff>
      ) => {
        const { handoff } = read(key)
        if (handoff?.messageId !== messageId) return
        update(key, { handoff: { ...handoff, ...change } })
      }

      // One message the agent can work through: the code first, as the blocks the transcript renders, then each comment by where it is.
      const message = (
        comments: ReadonlyArray<AgentComment>,
        headSha: string | null
      ): string => {
        const count =
          comments.length === 1 ? "a comment" : `${comments.length} comments`
        const head = headSha ? ` at \`${headSha.slice(0, 7)}\`` : ""
        const items = comments.map((comment, index) => {
          const stale =
            headSha && comment.headSha && comment.headSha !== headSha
              ? ` (written at \`${comment.headSha.slice(0, 7)}\`, before the latest push)`
              : ""
          return `${index + 1}. \`${comment.path}:${commentRangeLabel(comment.range)}\`${stale}: ${comment.body.trim()}`
        })
        return serializeExcerpts(
          [
            `I reviewed this pull request${head} and left ${count} for you. Work through them together.`,
            ...items,
          ].join("\n\n"),
          comments.flatMap((comment) => comment.excerpts)
        )
      }

      return {
        sessions: {},
        start: (key) => update(key, { active: true, error: null }),
        abandon: (key) =>
          update(key, { active: false, comments: [], error: null }),
        add: (key, comment) =>
          update(key, {
            active: true,
            comments: [...read(key).comments, comment],
          }),
        edit: (key, id, body) =>
          update(key, {
            comments: read(key).comments.map((comment) =>
              comment.id === id ? { ...comment, body } : comment
            ),
          }),
        attachExcerpts: (key, id, excerpts) =>
          update(key, {
            comments: read(key).comments.map((comment) =>
              comment.id === id ? { ...comment, excerpts } : comment
            ),
          }),
        remove: (key, id) =>
          update(key, {
            comments: read(key).comments.filter((comment) => comment.id !== id),
          }),
        send: (key, delivery, headSha) => {
          const session = read(key)
          const comments = session.comments.filter((comment) =>
            comment.body.trim()
          )
          const blank = session.comments.filter(
            (comment) => !comment.body.trim()
          )
          if (session.handoff || comments.length === 0) return
          update(key, {
            active: blank.length > 0,
            comments: blank,
            error: null,
            handoff: {
              messageId: crypto.randomUUID(),
              text: message(comments, headSha),
              delivery,
              comments,
              status: "sending",
              withdrawing: false,
            },
          })
        },
        dispatched: (key, messageId) =>
          updateHandoff(key, messageId, { status: "sent" }),
        withdraw: (key) => {
          const { handoff } = read(key)
          if (handoff?.status === "scheduled")
            updateHandoff(key, handoff.messageId, { withdrawing: true })
        },
        keepScheduled: (key, messageId) =>
          updateHandoff(key, messageId, { withdrawing: false }),
        restore: (key, messageId, error) => {
          const session = read(key)
          if (session.handoff?.messageId !== messageId) return
          write(key, {
            active: true,
            comments: [...session.handoff.comments, ...session.comments],
            handoff: null,
            error,
          })
        },
        observe: (key, messageId, seen) => {
          const { handoff } = read(key)
          if (handoff?.messageId !== messageId || handoff.status === "sending")
            return null
          const { restore } = get()
          switch (seen.kind) {
            case "sending":
              return null
            case "queued":
              if (handoff.status !== "scheduled")
                updateHandoff(key, messageId, { status: "scheduled" })
              return null
            case "delivered":
              if (!seen.running) {
                update(key, { handoff: null })
                return "finished"
              }
              if (handoff.status !== "delivered")
                updateHandoff(key, messageId, {
                  status: "delivered",
                  withdrawing: false,
                })
              return null
            case "failed":
              restore(key, messageId, seen.error)
              return "failed"
            case "missing":
              // Withdrawals restore the drafts themselves. A scheduled batch
              // the chat can't see has left the queue for its run, or ran
              // before the loaded part of the transcript; once nothing is
              // running, it has been handled.
              if (handoff.status === "scheduled") {
                if (seen.running) return null
                update(key, { handoff: null })
                return "finished"
              }
              if (handoff.status === "sent" && seen.settled) {
                restore(
                  key,
                  messageId,
                  "Couldn't confirm Open SWE received these comments, so they're back in your drafts. Check the chat before sending again."
                )
                return "lost"
              }
              return null
          }
        },
        clearError: (key) => update(key, { error: null }),
      }
    },
    {
      name: STORAGE_KEY,
      version: STORAGE_VERSION,
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ sessions: state.sessions }),
      merge: (persisted, current) => ({
        ...current,
        sessions: ReviewSessionStorage.parse(persisted),
      }),
    }
  )
)

export function useReviewSession(pr: PullRequestRef): ReviewSession {
  const key = reviewSessionKey(pr)
  return useReviewSessions((state) => state.sessions[key] ?? idle)
}
