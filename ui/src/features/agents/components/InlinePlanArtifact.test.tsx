/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { InlinePlanArtifact } from "./InlinePlanArtifact"

const navigate = vi.fn()
const getPlan = vi.fn()
const dismissPlan = vi.fn()
let plan = {
  threadId: "thread-1",
  status: "ready",
  html: "",
  markdown: "Implementation plan",
  approvedBy: null,
  approvedAt: null,
  dismissed: false,
  user: { id: "alice", login: "alice", email: null, name: "Alice" },
}

vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryFn }: { queryFn: () => unknown }) => ({ data: queryFn() }),
  useQueryClient: () => ({
    setQueryData: (
      _key: readonly string[],
      update: (value: typeof plan) => typeof plan
    ) => {
      plan = update(plan)
    },
  }),
  useMutation: ({
    mutationFn,
    onSuccess,
  }: {
    mutationFn: () => Promise<unknown>
    onSuccess: () => void
  }) => ({
    isPending: false,
    mutate: () => void mutationFn().then(onSuccess),
  }),
}))
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
}))
vi.mock("@/lib/plan", () => ({
  getPlan: () => getPlan(),
  dismissPlan: () => dismissPlan(),
}))
vi.mock("@/features/agents/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}))

beforeEach(() => {
  plan = { ...plan, dismissed: false }
  getPlan.mockImplementation(() => plan)
  dismissPlan.mockResolvedValue({ dismissed: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("InlinePlanArtifact", () => {
  it("persists dismissal through the plan API", async () => {
    const { rerender } = render(<InlinePlanArtifact threadId="thread-1" />)
    await screen.findByTestId("inline-plan-artifact")

    fireEvent.click(screen.getByRole("button", { name: "Dismiss plan" }))
    await waitFor(() => expect(dismissPlan).toHaveBeenCalledOnce())
    rerender(<InlinePlanArtifact threadId="thread-1" />)

    expect(screen.queryByTestId("inline-plan-artifact")).toBeNull()
  })
})
