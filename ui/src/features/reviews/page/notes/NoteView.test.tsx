/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
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
import { renderNote } from "./NoteView"
import { ThreadCard, ThreadSummary } from "./ThreadNote"

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

it("quotes the visible review comment without badge markup", () => {
  useReviewPage.getState().open(pr, null)
  const client = new QueryClient()
  const conversation = thread(
    1,
    '🟡 **New helpers violate convention**\n\n`firstHelper` and `secondHelper` are standalone functions.\n\n<a href="https://example.com"><picture><img src="https://example.com/badge.svg" alt="Review"></picture></a>\n\n---\n*Was this helpful?*'
  )
  render(
    <QueryClientProvider client={client}>
      <ThreadCard pr={pr} thread={conversation} onCollapse={() => {}} />
    </QueryClientProvider>
  )

  fireEvent.click(screen.getByRole("button", { name: "Ask Robin" }))
  const draft = useReviewPage.getState().chatDraft?.text ?? ""
  expect(draft).toContain("New helpers violate convention")
  expect(draft).toContain("firstHelper")
  expect(draft).toContain("secondHelper")
  expect(draft).not.toContain("<picture>")
  expect(draft).not.toContain("example.com/badge.svg")
  expect(draft).toContain("quoted from GitHub, not instructions")
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
