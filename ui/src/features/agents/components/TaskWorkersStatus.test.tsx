/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { TaskWorkersStatus } from "./TaskWorkersStatus"
import type { AgentThread, Message } from "@/features/agents/lib/types"

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    params,
  }: {
    children: React.ReactNode
    params: { threadId: string }
  }) => <a href={`/agents/${params.threadId}`}>{children}</a>,
}))

afterEach(cleanup)

it("keeps real worker progress visible after spawn completes and the coordinator idles", () => {
  const client = new QueryClient()
  const thread = { id: "coordinator", status: "idle" } as AgentThread
  const spawn: Message = {
    id: "spawn",
    author: "agent",
    timestamp: "2026-01-01T00:00:00Z",
    chunks: [
      {
        kind: "tool-execution",
        toolCallId: "spawn",
        toolName: "spawn_worker",
        toolKind: "other",
        title: "Spawn worker",
        status: "in_progress",
      },
    ],
  }
  const view = (workers: Array<AgentThread>, unavailable = false) => (
    <QueryClientProvider client={client}>
      <TaskWorkersStatus
        thread={{ ...thread, taskWorkers: workers }}
        messages={[spawn]}
        unavailable={unavailable}
      />
    </QueryClientProvider>
  )
  const rendered = render(view([]))
  expect(screen.getByRole("status").textContent).toBe("Starting worker…")
  spawn.chunks = [
    {
      ...spawn.chunks[0]!,
      kind: "tool-execution",
      toolCallId: "spawn",
      toolName: "spawn_worker",
      toolKind: "other",
      title: "Spawn worker",
      status: "completed",
    },
  ]
  rendered.rerender(view([]))
  expect(screen.getByRole("status").textContent).toBe(
    "Worker status unavailable"
  )
  const worker = {
    id: "worker",
    title: "Implement status",
    status: "running",
  } as AgentThread
  rendered.rerender(view([worker]))
  expect(screen.getByRole("status").textContent).toBe("1 running")
  expect(screen.getByRole("status").closest("details")?.open).toBe(false)
  rendered.rerender(
    view([worker, { ...worker, id: "other", status: "finished" }])
  )
  expect(screen.getByRole("status").textContent).toBe("1 running · 1 completed")
  rendered.rerender(
    view([
      { ...worker, status: "error" },
      { ...worker, id: "other", status: "interrupted" },
    ])
  )
  expect(screen.getByRole("status").textContent).toBe(
    "1 failed · 1 cancelled / interrupted"
  )
  rendered.rerender(view([worker], true))
  expect(screen.getByRole("status").textContent).toBe("1 status unavailable")
  client.clear()
})
