/** @vitest-environment jsdom */

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AutomationsList } from "./AutomationsList"

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useNavigate: () => vi.fn(),
}))
vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { user_id: "me", is_admin: true } }),
}))
vi.mock("@/lib/optimistic", () => ({ usePendingVariables: () => [] }))
vi.mock("@/features/agents/lib/queries", () => ({
  agentMutationKeys: { updateSchedule: ["update"] },
  useAgentSchedules: () => ({
    data: [
      {
        id: "mine",
        name: "My automation",
        ownerId: "me",
        enabled: true,
        triggers: [],
      },
      {
        id: "theirs",
        name: "Their automation",
        ownerId: "them",
        enabled: true,
        triggers: [],
      },
      {
        id: "system",
        name: "System automation",
        ownerId: "me",
        system: true,
        enabled: true,
        triggers: [],
      },
    ],
    isLoading: false,
  }),
  useTriggerAgentSchedule: () => ({}),
  useUpdateAgentSchedule: () => ({}),
  useWorkspaceOptions: () => ({ data: { workspaces: [] } }),
}))
vi.mock("./AutomationRuns", () => ({ AutomationRuns: () => null }))
vi.mock("./AutomationTemplates", () => ({ AutomationTemplates: () => null }))

afterEach(cleanup)

describe("AutomationsList", () => {
  it("shows the right automations in each admin view", () => {
    render(<AutomationsList tab="overview" onTabChange={vi.fn()} />, {
      wrapper: TooltipProvider,
    })
    expect(screen.getByText("My automation")).toBeTruthy()
    expect(screen.queryByText("Their automation")).toBeNull()
    expect(screen.queryByText("System automation")).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "System" }))
    expect(screen.queryByText("My automation")).toBeNull()
    expect(screen.getByText("System automation")).toBeTruthy()

    fireEvent.click(screen.getByRole("button", { name: "All" }))
    expect(screen.getByText("My automation")).toBeTruthy()
    expect(screen.getByText("Their automation")).toBeTruthy()
    expect(screen.getByText("System automation")).toBeTruthy()
  })
})
