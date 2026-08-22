/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it } from "vitest"

import {
  DEFAULT_CHAT_TITLE,
  chatStorageKey,
  closeConversation,
  deriveChatTitle,
  loadChatState,
  nameConversation,
  saveChatState,
  selectConversation,
  startConversation,
} from "./chatStorage"
import type { ChatState, Conversation } from "./chatStorage"

const KEY = chatStorageKey("acme", "repo", 7)

function conversation(id: string, title: string, createdAt = 1): Conversation {
  return { id, title, createdAt }
}

function state(
  conversations: Array<Conversation>,
  activeId = conversations[0]?.id ?? ""
): ChatState {
  return { conversations, activeId }
}

beforeEach(() => window.localStorage.clear())

describe("chatStorageKey", () => {
  it("scopes conversations to one pull request", () => {
    expect(chatStorageKey("acme", "repo", 7)).toBe(
      "osw:review-chat:acme/repo/7"
    )
    expect(chatStorageKey("acme", "repo", 8)).toBe(
      "osw:review-chat:acme/repo/8"
    )
  })
})

describe("deriveChatTitle", () => {
  it("flattens whitespace", () => {
    expect(deriveChatTitle("  why   is\nthis slow? ")).toBe("why is this slow?")
  })

  it("truncates to 60 characters", () => {
    expect(deriveChatTitle("x".repeat(80))).toBe("x".repeat(60))
  })

  it("falls back to the default title for empty text", () => {
    expect(deriveChatTitle("   ")).toBe(DEFAULT_CHAT_TITLE)
  })
})

describe("loadChatState", () => {
  it("starts a fresh draft when nothing is stored", () => {
    const loaded = loadChatState(KEY)

    expect(loaded.conversations).toHaveLength(1)
    expect(loaded.conversations[0]?.title).toBe(DEFAULT_CHAT_TITLE)
    expect(loaded.activeId).toBe(loaded.conversations[0]?.id)
  })

  it("round-trips saved conversations", () => {
    const saved = state(
      [conversation("a", "First"), conversation("b", "Second")],
      "b"
    )

    saveChatState(KEY, saved)

    expect(loadChatState(KEY)).toEqual(saved)
  })

  it("drops malformed conversations and falls back to the remaining ones", () => {
    window.localStorage.setItem(
      KEY,
      JSON.stringify({
        conversations: [{ id: "a" }, conversation("b", "Second")],
        activeId: "a",
      })
    )

    expect(loadChatState(KEY)).toEqual(
      state([conversation("b", "Second")], "b")
    )
  })

  it("starts a fresh draft when the stored payload is not JSON", () => {
    window.localStorage.setItem(KEY, "{not json")

    const loaded = loadChatState(KEY)

    expect(loaded.conversations).toHaveLength(1)
    expect(loaded.conversations[0]?.title).toBe(DEFAULT_CHAT_TITLE)
  })
})

describe("selectConversation", () => {
  it("activates a conversation already open", () => {
    const open = state([
      conversation("a", "First"),
      conversation("b", "Second"),
    ])

    expect(selectConversation(open, conversation("b", "Second"))).toEqual(
      state([conversation("a", "First"), conversation("b", "Second")], "b")
    )
  })

  it("adds a conversation recovered from the server", () => {
    const open = state([conversation("a", "First")])

    expect(selectConversation(open, conversation("c", "Recovered", 5))).toEqual(
      state(
        [conversation("a", "First"), conversation("c", "Recovered", 5)],
        "c"
      )
    )
  })
})

describe("startConversation", () => {
  it("reuses the active draft instead of stacking empty tabs", () => {
    const open = state([conversation("a", DEFAULT_CHAT_TITLE)])

    expect(startConversation(open)).toBe(open)
  })

  it("appends a draft when the active conversation has been used", () => {
    const open = state([conversation("a", "First")])

    const next = startConversation(open)

    expect(next.conversations).toHaveLength(2)
    expect(next.conversations[1]?.title).toBe(DEFAULT_CHAT_TITLE)
    expect(next.activeId).toBe(next.conversations[1]?.id)
  })
})

describe("nameConversation", () => {
  it("names a draft from its first message", () => {
    const open = state([conversation("a", DEFAULT_CHAT_TITLE)])

    expect(nameConversation(open, "a", "Why is this slow?")).toEqual(
      state([conversation("a", "Why is this slow?")], "a")
    )
  })

  it("leaves an already-named conversation alone", () => {
    const open = state([conversation("a", "First")])

    expect(nameConversation(open, "a", "Second")).toBe(open)
  })
})

describe("closeConversation", () => {
  it("activates the previous tab", () => {
    const open = state(
      [
        conversation("a", "First"),
        conversation("b", "Second"),
        conversation("c", "Third"),
      ],
      "b"
    )

    expect(closeConversation(open, "b")).toEqual(
      state([conversation("a", "First"), conversation("c", "Third")], "a")
    )
  })

  it("keeps the active tab when another one closes", () => {
    const open = state(
      [conversation("a", "First"), conversation("b", "Second")],
      "b"
    )

    expect(closeConversation(open, "a")).toEqual(
      state([conversation("b", "Second")], "b")
    )
  })

  it("opens a fresh draft when the last tab closes", () => {
    const open = state([conversation("a", "First")])

    const next = closeConversation(open, "a")

    expect(next.conversations).toHaveLength(1)
    expect(next.conversations[0]?.title).toBe(DEFAULT_CHAT_TITLE)
    expect(next.activeId).toBe(next.conversations[0]?.id)
  })

  it("ignores a conversation that is not open", () => {
    const open = state([conversation("a", "First")])

    expect(closeConversation(open, "zz")).toBe(open)
  })
})
