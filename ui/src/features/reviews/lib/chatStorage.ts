/**
 * The PR chat's conversation list, persisted per pull request.
 *
 * The client owns this list. Each conversation is a client-minted thread id;
 * the thread is created server-side lazily on its first message. The server
 * thread list is only used to recover conversations on a fresh browser and to
 * reconcile titles — it is never the sole source for the tabs, so an empty or
 * lagging server response can no longer make open chats vanish.
 */

import { useCallback, useState } from "react"

export interface Conversation {
  id: string
  title: string
  createdAt: number
}

export interface ChatState {
  conversations: Array<Conversation>
  activeId: string
}

// Kept in sync with the backend's `_derive_title` so the optimistic tab label
// matches the title the server persists for the thread.
export const DEFAULT_CHAT_TITLE = "New chat"
const TITLE_MAX_CHARS = 60

export function deriveChatTitle(text: string): string {
  const flattened = text.trim().split(/\s+/).join(" ")
  return flattened ? flattened.slice(0, TITLE_MAX_CHARS) : DEFAULT_CHAT_TITLE
}

export function chatStorageKey(
  owner: string,
  repo: string,
  number: number
): string {
  return `osw:review-chat:${owner}/${repo}/${number}`
}

export function newConversation(): Conversation {
  return {
    id: crypto.randomUUID(),
    title: DEFAULT_CHAT_TITLE,
    createdAt: Date.now(),
  }
}

function isConversation(value: unknown): value is Conversation {
  if (!value || typeof value !== "object") return false
  const candidate = value as Record<string, unknown>
  return (
    typeof candidate.id === "string" &&
    typeof candidate.title === "string" &&
    typeof candidate.createdAt === "number"
  )
}

export function loadChatState(key: string): ChatState {
  if (typeof window !== "undefined") {
    try {
      const raw = window.localStorage.getItem(key)
      if (raw) {
        const parsed: unknown = JSON.parse(raw)
        const source = (parsed ?? {}) as Record<string, unknown>
        const conversations = Array.isArray(source.conversations)
          ? source.conversations.filter(isConversation)
          : []
        const first = conversations[0]
        if (first) {
          const activeId =
            typeof source.activeId === "string" &&
            conversations.some((c) => c.id === source.activeId)
              ? source.activeId
              : first.id
          return { conversations, activeId }
        }
      }
    } catch {
      /* fall through to a fresh draft */
    }
  }
  const draft = newConversation()
  return { conversations: [draft], activeId: draft.id }
}

export function saveChatState(key: string, state: ChatState): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(key, JSON.stringify(state))
  } catch {
    /* ignore quota / availability errors */
  }
}

export function selectConversation(
  state: ChatState,
  conversation: Conversation
): ChatState {
  return {
    conversations: state.conversations.some((c) => c.id === conversation.id)
      ? state.conversations
      : [...state.conversations, conversation],
    activeId: conversation.id,
  }
}

/** Reuses a pristine, never-sent draft instead of stacking empty tabs. */
export function startConversation(state: ChatState): ChatState {
  const active = state.conversations.find((c) => c.id === state.activeId)
  if (active && active.title === DEFAULT_CHAT_TITLE) return state
  const draft = newConversation()
  return {
    conversations: [...state.conversations, draft],
    activeId: draft.id,
  }
}

/** Names a conversation from its first message; later messages don't rename it. */
export function nameConversation(
  state: ChatState,
  id: string,
  title: string
): ChatState {
  const current = state.conversations.find((c) => c.id === id)
  if (!current || current.title !== DEFAULT_CHAT_TITLE) return state
  return {
    ...state,
    conversations: state.conversations.map((c) =>
      c.id === id ? { ...c, title } : c
    ),
  }
}

export function closeConversation(state: ChatState, id: string): ChatState {
  const index = state.conversations.findIndex((c) => c.id === id)
  if (index === -1) return state
  const remaining = state.conversations.filter((c) => c.id !== id)
  const fallback = remaining[Math.max(0, index - 1)]
  if (!fallback) {
    const draft = newConversation()
    return { conversations: [draft], activeId: draft.id }
  }
  return {
    conversations: remaining,
    activeId: state.activeId === id ? fallback.id : state.activeId,
  }
}

export function useConversations(key: string) {
  const [state, setState] = useState<ChatState>(() => loadChatState(key))

  const update = useCallback(
    (fn: (prev: ChatState) => ChatState) => {
      setState((prev) => {
        const next = fn(prev)
        if (next === prev) return prev
        saveChatState(key, next)
        return next
      })
    },
    [key]
  )

  return {
    ...state,
    select: useCallback(
      (conversation: Conversation) =>
        update((prev) => selectConversation(prev, conversation)),
      [update]
    ),
    newChat: useCallback(() => update(startConversation), [update]),
    nameConversation: useCallback(
      (id: string, title: string) =>
        update((prev) => nameConversation(prev, id, title)),
      [update]
    ),
    close: useCallback(
      (id: string) => update((prev) => closeConversation(prev, id)),
      [update]
    ),
  }
}
