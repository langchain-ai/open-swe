// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { WorkspaceTicketIntakeSection } from "./WorkspaceTicketIntakeSection"
import type { ReactNode } from "react"

const mockApi = vi.hoisted(() => ({
  getTicketIntake: vi.fn(),
  saveTicketIntake: vi.fn(),
  testTicketIntakeConnection: vi.fn(),
  previewTicketIntake: vi.fn(),
}))

vi.mock("@/lib/api", () => ({
  api: mockApi,
}))

function renderWithQueryClient(children: ReactNode) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

describe("WorkspaceTicketIntakeSection", () => {
  afterEach(() => cleanup())

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getTicketIntake.mockResolvedValue({
      provider: "linear",
      credential: {
        provider: "linear",
        available: false,
        source: null,
      },
      tracker_config: {
        team_ids: [],
        team_keys: ["SPORT"],
        team_names: [],
        linear_project_ids: ["linear-sports"],
        linear_project_names: [],
      },
      queue_eligibility_policy: {
        labels: ["agent-ready"],
        ready_states: ["ready"],
        excluded_statuses: ["done", "completed"],
        required_fields: ["description"],
        missing_readiness: "not-ready",
        polling_interval_minutes: 5,
      },
    })
    mockApi.saveTicketIntake.mockImplementation((_projectId, body) =>
      Promise.resolve({
        provider: "linear",
        credential: {
          provider: "linear",
          available: true,
          source: "LINEAR_API_KEY",
        },
        tracker_config: {
          team_ids: body.team_ids,
          team_keys: body.team_keys,
          team_names: body.team_names,
          linear_project_ids: body.linear_project_ids,
          linear_project_names: body.linear_project_names,
        },
        queue_eligibility_policy: {
          labels: body.labels,
          ready_states: body.ready_states,
          excluded_statuses: body.excluded_statuses,
          required_fields: body.required_fields,
          missing_readiness: body.missing_readiness,
          polling_interval_minutes: body.polling_interval_minutes,
        },
      })
    )
    mockApi.testTicketIntakeConnection.mockResolvedValue({
      status: "missing_credentials",
      provider: "linear",
      teams: [],
      projects: [],
      error: "LINEAR_API_KEY is not configured.",
    })
    mockApi.previewTicketIntake.mockResolvedValue({
      status: "previewed",
      provider: "linear",
      counts: { queued: 1, "not-ready": 1, blocked: 1, ignored: 1 },
      items: [
        { action: "queued", identifier: "SPORT-1", title: "Ready item" },
        { action: "not-ready", identifier: "SPORT-2", title: "Missing label" },
      ],
    })
  })

  it("renders Linear intake status and missing credential state", async () => {
    renderWithQueryClient(
      <WorkspaceTicketIntakeSection projectId="sports-cms" />
    )

    expect(await screen.findByText("Ticket Intake")).not.toBeNull()
    expect(
      await screen.findByText(/Connect a Linear provider token/)
    ).not.toBeNull()
    expect(screen.getByLabelText("Linear team keys")).toHaveProperty(
      "value",
      "SPORT"
    )
    expect(screen.getByLabelText("Linear project IDs")).toHaveProperty(
      "value",
      "linear-sports"
    )
  })

  it("reports provider PAT as the Linear credential source", async () => {
    mockApi.getTicketIntake.mockResolvedValueOnce({
      provider: "linear",
      credential: {
        provider: "linear",
        available: true,
        source: "provider_pat",
      },
      tracker_config: {
        team_ids: [],
        team_keys: ["SPORT"],
        team_names: [],
        linear_project_ids: ["linear-sports"],
        linear_project_names: [],
      },
      queue_eligibility_policy: {
        labels: ["agent-ready"],
        ready_states: ["ready"],
        excluded_statuses: ["done", "completed"],
        required_fields: ["description"],
        missing_readiness: "not-ready",
        polling_interval_minutes: 5,
      },
    })

    renderWithQueryClient(
      <WorkspaceTicketIntakeSection projectId="sports-cms" />
    )

    expect(await screen.findByText("provider_pat is configured.")).not.toBeNull()
    expect(await screen.findByText("Available")).not.toBeNull()
  })

  it("saves tracker config and queue eligibility policy", async () => {
    renderWithQueryClient(
      <WorkspaceTicketIntakeSection projectId="sports-cms" />
    )

    const teamKeys = await screen.findByLabelText("Linear team keys")
    await waitFor(() => expect(teamKeys).toHaveProperty("value", "SPORT"))
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save" })).toHaveProperty(
        "disabled",
        false
      )
    )
    fireEvent.change(teamKeys, { target: { value: "SPORT, CMS" } })
    fireEvent.change(screen.getByLabelText("Ready labels"), {
      target: { value: "agent-ready, cms-ready" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(mockApi.saveTicketIntake).toHaveBeenCalledWith("sports-cms", {
        provider: "linear",
        team_ids: [],
        team_keys: ["SPORT", "CMS"],
        team_names: [],
        linear_project_ids: ["linear-sports"],
        linear_project_names: [],
        labels: ["agent-ready", "cms-ready"],
        ready_states: ["ready"],
        excluded_statuses: ["done", "completed"],
        required_fields: ["description"],
        missing_readiness: "not-ready",
        polling_interval_minutes: 5,
      })
    )
  })

  it("runs read-only connection test and intake preview", async () => {
    mockApi.testTicketIntakeConnection.mockResolvedValueOnce({
      status: "connected",
      provider: "linear",
      teams: [{ id: "team-1", key: "SPORT", name: "Sports" }],
      projects: [{ id: "linear-sports", name: "Sports CMS" }],
    })
    renderWithQueryClient(
      <WorkspaceTicketIntakeSection projectId="sports-cms" />
    )

    await waitFor(() =>
      expect(screen.getByLabelText("Linear team keys")).toHaveProperty(
        "value",
        "SPORT"
      )
    )
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Test connection" })
      ).toHaveProperty("disabled", false)
    )
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }))
    expect(await screen.findByText("Teams")).not.toBeNull()
    expect(await screen.findByText("Sports CMS")).not.toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Preview intake" }))
    expect(await screen.findByText("Ready item")).not.toBeNull()
    expect(await screen.findByText("SPORT-2")).not.toBeNull()
  })

  it("shows API errors from save", async () => {
    mockApi.saveTicketIntake.mockRejectedValueOnce(
      new Error("at least one Linear team or project selector is required")
    )
    renderWithQueryClient(
      <WorkspaceTicketIntakeSection projectId="sports-cms" />
    )

    await waitFor(() =>
      expect(screen.getByLabelText("Linear team keys")).toHaveProperty(
        "value",
        "SPORT"
      )
    )
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save" })).toHaveProperty(
        "disabled",
        false
      )
    )
    fireEvent.change(screen.getByLabelText("Linear team keys"), {
      target: { value: "" },
    })
    fireEvent.change(screen.getByLabelText("Linear project IDs"), {
      target: { value: "" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    expect(
      await screen.findByText(
        "at least one Linear team or project selector is required"
      )
    ).not.toBeNull()
  })
})
