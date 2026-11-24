/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { ThreadSourceProvider } from "@/features/agents/lib/threadSource/ThreadSourceProvider"
import type { AgentThread, Message } from "@/features/agents/lib/types"
import { SubagentThreadView } from "./SubagentThreadView"

vi.mock("@/features/agents/components/messages", () => ({
  Messages: ({ messages }: { messages: Array<Message> }) => (
    <ol aria-label="Messages">
      {messages.map((message) => (
        <li key={message.id}>
          {[
            `${message.author}:`,
            ...message.chunks.map((chunk) =>
              chunk.kind === "tool-execution"
                ? `[${chunk.title} ${chunk.status}]`
                : chunk.kind === "text"
                  ? chunk.text
                  : ""
            ),
          ].join(" ")}
        </li>
      ))}
    </ol>
  ),
}))

const THREAD: AgentThread = {
  id: "1dd69115-f4b9-507f-b4d5-9f355f9f5ba0",
  title: "Look into the repository",
} as AgentThread
const RUN_ID = "run-live"
const SAVED_UNDER = "tools:4c1f2b7e"

const failure = {
  type: "tool",
  id: "failure",
  tool_call_id: "call-layout",
  name: "task",
  status: "error",
  content: '{"status": "failed"}',
}
const root = [
  { type: "human", id: "ask", content: "Look into the repository" },
  {
    type: "ai",
    id: "delegate",
    content: "",
    tool_calls: [
      {
        id: "call-layout",
        name: "task",
        args: {
          description: "Inspect the repository layout",
          subagent_type: "general-purpose",
        },
      },
    ],
  },
  failure,
  { type: "ai", id: "failed", content: "The subagent failed." },
  { type: "human", id: "retry", content: "Try another way" },
]
const subagent = [
  { type: "human", id: "prompt", content: "Inspect the repository layout" },
  {
    type: "ai",
    id: "look",
    content: "Looking around.",
    tool_calls: [{ id: "call-ls", name: "execute", args: { command: "ls" } }],
  },
  {
    type: "tool",
    id: "listing",
    tool_call_id: "call-ls",
    content: "README.md",
  },
]

const state = (
  messages: Array<object>,
  id = "head",
  tasks: Array<object> = []
) => ({
  values: { messages },
  next: [],
  tasks,
  metadata: { source: "loop" },
  checkpoint: { checkpoint_id: id, checkpoint_ns: "" },
})

function stubAgentServer() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(
        input instanceof Request ? input.url : String(input),
        location.href
      )
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), {
          status,
          headers: { "content-type": "application/json" },
        })
      const { pathname } = url
      if (pathname.endsWith(`/runs/${RUN_ID}/stream`))
        return new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(
                new TextEncoder().encode(
                  `event: metadata\ndata: {"run_id": "${RUN_ID}"}\nid: 0\n\n`
                )
              )
            },
          }),
          { headers: { "content-type": "text/event-stream" } }
        )
      if (pathname.endsWith(`/runs/${RUN_ID}`))
        return json({ run_id: RUN_ID, thread_id: THREAD.id, status: "running" })
      if (pathname.endsWith("/runs"))
        return json(
          url.searchParams.get("status") === "running"
            ? [{ run_id: RUN_ID, thread_id: THREAD.id, status: "running" }]
            : []
        )
      if (pathname.endsWith("/info")) return json({})
      if (pathname.endsWith("/stream")) return json({}, 404)
      if (pathname.endsWith("/history")) {
        const { checkpoint } = JSON.parse(String(init?.body))
        if (checkpoint?.checkpoint_ns === SAVED_UNDER)
          return json([state(subagent)])
        if (checkpoint?.checkpoint_ns) return json([])
        return json([
          state(root),
          state(root.slice(0, 2), "dispatch", [
            {
              id: SAVED_UNDER.slice("tools:".length),
              name: "tools",
              path: ["__pregel_push", 0, false],
              interrupts: [],
              result: { messages: [failure] },
            },
          ]),
        ])
      }
      await new Promise((resolve) => setTimeout(resolve, 30))
      return json(state(root))
    })
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it("shows an earlier run's failed subagent on a page that opens while a later run streams", async () => {
  stubAgentServer()
  const router = createRouter({
    routeTree: createRootRoute({
      component: () => (
        <ThreadSourceProvider threadId={THREAD.id} transcript={false}>
          <SubagentThreadView thread={THREAD} subagentId="call-layout" />
        </ThreadSourceProvider>
      ),
    }),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  })
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )

  await waitFor(
    () =>
      expect(
        Array.from(
          screen.getByRole("list", { name: "Messages" }).querySelectorAll("li"),
          (row) => row.textContent
        )
      ).toEqual([
        "user: Inspect the repository layout",
        "agent: Looking around. [ls completed]",
      ]),
    { timeout: 5_000 }
  )
})
