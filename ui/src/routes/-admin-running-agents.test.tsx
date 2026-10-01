/** @vitest-environment jsdom */

import { QueryClientProvider, useMutation } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, expect, it, vi } from "vitest"

import { reportError } from "@/lib/errorReporting"
import { makeQueryClient } from "@/lib/query"
import { RunningAgentsSection } from "./admin"

const cancelThread = vi.hoisted(() => vi.fn<(id: string) => Promise<void>>())

vi.mock("@/lib/errorReporting", () => ({ reportError: vi.fn() }))
vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  Link: ({ children }: { children: ReactNode }) => <a>{children}</a>,
}))
vi.mock(import("@/features/agents/lib/queries"), async (importOriginal) => ({
  ...(await importOriginal()),
  useThreadsPage: () =>
    ({
      data: {
        items: [
          { id: "t1", title: "First", repoFullName: "acme/api" },
          { id: "t2", title: "Second", repoFullName: "acme/web" },
        ],
      },
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    }) as never,
  useAdminCancelAgentThread: () =>
    useMutation({ mutationFn: cancelThread }) as never,
}))

afterEach(() => {
  cleanup()
  cancelThread.mockReset()
})

function renderSection() {
  render(
    <QueryClientProvider client={makeQueryClient()}>
      <RunningAgentsSection />
    </QueryClientProvider>
  )
}

const killButtons = () => screen.queryAllByRole("button", { name: "Kill" })

it("hides a killed thread at once and leaves the others killable", async () => {
  cancelThread.mockReturnValue(new Promise(() => {}))
  renderSection()

  fireEvent.click(killButtons()[0]!)

  expect(screen.queryByText("First")).toBeNull()
  expect(screen.getByText("1 running")).toBeTruthy()
  const [remaining] = killButtons()
  expect(remaining!.hasAttribute("disabled")).toBe(false)
  fireEvent.click(remaining!)
  await waitFor(() =>
    expect(cancelThread.mock.calls.map(([id]) => id)).toEqual(["t1", "t2"])
  )
  expect(screen.getByText("0 running")).toBeTruthy()
})

it("brings the thread back and reports the error when the kill fails", async () => {
  const failure = new Error("not allowed")
  cancelThread.mockRejectedValue(failure)
  renderSection()

  fireEvent.click(killButtons()[0]!)

  await screen.findByText("First")
  expect(reportError).toHaveBeenCalledWith(
    expect.objectContaining({ error: failure })
  )
  await waitFor(() => expect(killButtons()).toHaveLength(2))
})
