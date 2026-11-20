/** @vitest-environment jsdom */

import { useState } from "react"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AutomationsList, type AutomationsFilter } from "./AutomationsList"

const viewer = vi.hoisted(() => ({ user_id: "me", is_admin: true }))

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useNavigate: () => vi.fn(),
}))
vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: viewer }),
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
        createdBy: "alice",
        enabled: true,
        triggers: [],
      },
      {
        id: "theirs",
        name: "Their automation",
        ownerId: "them",
        createdBy: "bob",
        enabled: false,
        lastError: "Run failed",
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
}))
vi.mock("./AutomationRuns", () => ({ AutomationRuns: () => null }))
vi.mock("./AutomationTemplates", () => ({ AutomationTemplates: () => null }))

afterEach(() => {
  cleanup()
  viewer.is_admin = true
})

function TestAutomations({
  initialFilter = "mine",
  initialOwner,
}: {
  initialFilter?: AutomationsFilter
  initialOwner?: string
}) {
  const [filter, setFilter] = useState(initialFilter)
  const [owner, setOwner] = useState(initialOwner)
  return (
    <AutomationsList
      tab="overview"
      onTabChange={vi.fn()}
      chosenFilter={filter}
      onFilterChange={(nextFilter) => {
        setFilter(nextFilter)
        setOwner(undefined)
      }}
      ownerId={owner}
      onOwnerChange={setOwner}
    />
  )
}

describe("AutomationsList", () => {
  it("shows the right automations in each admin view", () => {
    render(<TestAutomations />, {
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

    fireEvent.click(screen.getByRole("combobox", { name: "Filter by user" }))
    fireEvent.click(screen.getByRole("option", { name: "bob" }))
    expect(screen.queryByText("My automation")).toBeNull()
    expect(screen.getByText("Their automation")).toBeTruthy()
    expect(screen.queryByText("System automation")).toBeNull()
    expect(screen.getByText("1 automation")).toBeTruthy()

    fireEvent.click(screen.getByRole("combobox", { name: "Filter by user" }))
    fireEvent.click(screen.getByRole("option", { name: "All users" }))
    expect(screen.getByText("3 automations")).toBeTruthy()

    fireEvent.click(screen.getByRole("button", { name: "Mine" }))
    expect(
      screen.queryByRole("combobox", { name: "Filter by user" })
    ).toBeNull()
    expect(screen.getByText("My automation")).toBeTruthy()
  })

  it("shows a recoverable empty state for a user without automations", () => {
    render(<TestAutomations initialFilter="all" initialOwner="removed" />, {
      wrapper: TooltipProvider,
    })
    expect(screen.getByText("No automations for this user")).toBeTruthy()
    expect(screen.getByText("0 automations")).toBeTruthy()
  })

  it("keeps member views scoped to their own automations", () => {
    viewer.is_admin = false
    render(<TestAutomations initialFilter="all" initialOwner="them" />, {
      wrapper: TooltipProvider,
    })
    expect(screen.getByText("My automation")).toBeTruthy()
    expect(screen.queryByText("Their automation")).toBeNull()
    expect(screen.queryByText("System automation")).toBeNull()
    expect(
      screen.queryByRole("combobox", { name: "Filter by user" })
    ).toBeNull()
  })
})
