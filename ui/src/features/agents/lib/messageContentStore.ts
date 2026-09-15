/**
 * Full message contents for a transcript that painted from the trimmed
 * state view. The trimmed view blanks large tool outputs and pasted images and
 * marks each blank; this cache fills them in, either in bulk from one
 * background read of the full state, or one message at a time when the user
 * expands something before that read lands.
 */
import { create } from "zustand"

import { agentsApi } from "./api"
import type { ThreadStateMessage } from "./api"

export type MessageContent = string | Array<Record<string, unknown>>

export const STUB_KEY = "open_swe_stub"

interface MessageContentState {
  contents: Record<string, Record<string, MessageContent>>
  put: (threadId: string, messageId: string, content: MessageContent) => void
  /** Fetch one message's full content; resolves `false` when it could not be loaded. */
  ensure: (threadId: string, messageId: string) => Promise<boolean>
  /** Load every large content of a thread from the full state in one read. */
  prefetch: (threadId: string) => Promise<void>
}

function isContent(value: unknown): value is MessageContent {
  return (
    typeof value === "string" ||
    (Array.isArray(value) &&
      value.every((item) => item !== null && typeof item === "object"))
  )
}

/** Only messages the trimmed view could have blanked are worth caching. */
function worthCaching(message: ThreadStateMessage): boolean {
  return message.type === "tool" || Array.isArray(message.content)
}

const inflight = new Map<string, Promise<boolean>>()
const prefetched = new Set<string>()

export const useMessageContentStore = create<MessageContentState>(
  (set, get) => ({
    contents: {},
    put: (threadId, messageId, content) =>
      set((state) => ({
        contents: {
          ...state.contents,
          [threadId]: { ...state.contents[threadId], [messageId]: content },
        },
      })),
    ensure: (threadId, messageId) => {
      if (get().contents[threadId]?.[messageId] !== undefined)
        return Promise.resolve(true)
      const key = `${threadId}/${messageId}`
      const pending = inflight.get(key)
      if (pending) return pending
      const request = agentsApi
        .getThreadMessage(threadId, messageId)
        .then((message) => {
          if (!isContent(message.content)) return false
          get().put(threadId, messageId, message.content)
          return true
        })
        .catch(() => false)
        .finally(() => inflight.delete(key))
      inflight.set(key, request)
      return request
    },
    prefetch: async (threadId) => {
      if (prefetched.has(threadId)) return
      prefetched.add(threadId)
      try {
        const state = await agentsApi.getThreadState(threadId, "full")
        const loaded: Record<string, MessageContent> = {}
        for (const message of state.values?.messages ?? []) {
          if (
            typeof message.id === "string" &&
            worthCaching(message) &&
            isContent(message.content)
          ) {
            loaded[message.id] = message.content
          }
        }
        set((current) => ({
          contents: {
            ...current.contents,
            [threadId]: { ...loaded, ...current.contents[threadId] },
          },
        }))
      } catch {
        // A failed prefetch only means expands fall back to per-message reads.
        prefetched.delete(threadId)
      }
    },
  })
)

/** Test hook: forget every cached content and in-flight request. */
export function resetMessageContentStore(): void {
  inflight.clear()
  prefetched.clear()
  useMessageContentStore.setState({ contents: {} })
}
