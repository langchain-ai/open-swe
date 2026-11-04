/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { api } from "@/lib/api"
import type {
  Conversation,
  ReviewThread,
} from "@/features/reviews/lib/conversationApi"
import { reviewKeys } from "@/features/reviews/lib/reviewKeys"
import type { Note } from "@/features/reviews/page/diffEntries"
import { useReviewPage } from "@/features/reviews/page/store"
import { Discussion } from "@/features/reviews/page/Discussion"
import { renderNote } from "./NoteView"
import { ThreadSummary } from "./ThreadNote"

const reply = vi.hoisted(() => vi.fn(() => new Promise<never>(() => {})))
vi.mock("@/features/reviews/lib/conversationApi", async (original) => ({
  ...(await original<object>()),
  replyToReviewThread: reply,
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const pr = { owner: "acme", repo: "app", number: 7 }

function thread(id: number, body: string): ReviewThread {
  return {
    id,
    node_id: `T${id}`,
    path: "src/a.ts",
    line: id,
    start_line: null,
    side: "RIGHT",
    original_line: id,
    diff_hunk: "@@ -1 +1 @@\n+line",
    outdated: false,
    resolved: false,
    comments: [
      {
        id,
        review_id: null,
        author: { login: "ana", avatar_url: "", bot: false },
        created_at: "2026-10-01T00:00:00Z",
        body,
        html_url: "",
      },
    ],
  }
}

// CodeView renders each line's notes into slots keyed by position.
function Slots({ notes }: { notes: Array<Exclude<Note, { kind: "step" }>> }) {
  return notes.map((note, index) => (
    <div key={index} data-testid="slot">
      {renderNote(pr, note)}
    </div>
  ))
}

it("announces a folded conversation's status and comment count", () => {
  const conversation = thread(1, "Keep the selected state")
  conversation.comments.push({ ...conversation.comments[0], id: 2 })
  const onOpen = vi.fn()
  const view = render(
    <ThreadSummary
      thread={{ ...conversation, resolved: true }}
      onOpen={onOpen}
    />
  )
  fireEvent.click(
    screen.getByRole("button", {
      name: "ana: Keep the selected state, 2 comments, Resolved",
      expanded: false,
    })
  )
  expect(onOpen).toHaveBeenCalledOnce()

  view.rerender(
    <ThreadSummary
      thread={{ ...conversation, outdated: true }}
      onOpen={onOpen}
      location="src/a.ts:1"
    />
  )
  expect(
    screen.getByRole("button", {
      name: "src/a.ts:1: ana: Keep the selected state, 2 comments, Outdated",
    })
  ).toBeTruthy()
})

it("shows review threads once until the open-conversation index is requested", () => {
  useReviewPage.getState().open(pr, null)
  const client = new QueryClient({
    defaultOptions: { queries: { enabled: false } },
  })
  const conversation = thread(1, "Scope the audience per workspace")
  conversation.comments[0].review_id = 7
  client.setQueryData(reviewKeys.conversation(pr), {
    items: [
      {
        kind: "review",
        id: 7,
        author: conversation.comments[0].author,
        created_at: conversation.comments[0].created_at,
        body: "",
        html_url: "",
        state: "COMMENTED",
      },
    ],
    threads: [conversation],
  } satisfies Conversation)
  client.setQueryData(reviewKeys.detail(pr), {
    findings: [],
    pr: { author: null },
  })
  render(
    <QueryClientProvider client={client}>
      <Discussion pr={pr} />
    </QueryClientProvider>
  )

  const index = screen.getByRole("button", { name: "Open conversations 1" })
  const rows = () =>
    screen.getAllByRole("button", { name: /Scope the audience/ })
  expect(index.getAttribute("aria-expanded")).toBe("false")
  expect(rows()).toHaveLength(1)
  fireEvent.click(index)
  expect(rows()).toHaveLength(2)
  fireEvent.click(index)
  expect(rows()).toHaveLength(1)
  act(() => useReviewPage.getState().showOpenConversations())
  expect(index.getAttribute("aria-expanded")).toBe("true")
  expect(rows()).toHaveLength(2)
})

it("keeps a half-written reply with its thread when a note is added above it", async () => {
  vi.spyOn(api, "me").mockRejectedValue(new Error("Signed out"))
  useReviewPage.getState().open(pr, null)
  const client = new QueryClient()
  const conversation: Conversation = {
    items: [],
    threads: [thread(1, "Thread A"), thread(2, "Thread B")],
  }
  client.setQueryData(reviewKeys.conversation(pr), conversation)
  const b = { kind: "thread", id: 2 } as const
  const view = render(
    <QueryClientProvider client={client}>
      <Slots notes={[b]} />
    </QueryClientProvider>
  )
  fireEvent.click(screen.getByRole("button", { name: "Reply…" }))
  fireEvent.change(screen.getByRole("textbox", { name: "Reply" }), {
    target: { value: "for B" },
  })

  view.rerender(
    <QueryClientProvider client={client}>
      <Slots notes={[{ kind: "thread", id: 1 }, b]} />
    </QueryClientProvider>
  )

  const [slotA, slotB] = screen.getAllByTestId("slot")
  expect(slotA!.textContent).toContain("Thread A")
  expect(slotA!.querySelector("textarea")).toBeNull()
  const box = slotB!.querySelector("textarea")!
  expect(box.value).toBe("for B")
  fireEvent.keyDown(box, { key: "Enter", metaKey: true })
  await waitFor(() => expect(reply).toHaveBeenCalledWith(pr, 2, "for B"))
})
