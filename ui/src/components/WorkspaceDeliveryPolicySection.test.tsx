// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { WorkspaceDeliveryPolicySection } from "./WorkspaceDeliveryPolicySection"

const mockApi = vi.hoisted(() => ({
  getWorkspaceDeliveryPolicy: vi.fn(),
  saveWorkspaceDeliveryPolicy: vi.fn(),
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

describe("WorkspaceDeliveryPolicySection", () => {
  afterEach(() => cleanup())

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getWorkspaceDeliveryPolicy.mockResolvedValue({
      project_id: "sports-cms",
      active: true,
      kill_switch: false,
      gate_policy: {
        agent_review: true,
        qa_evidence: true,
        blocking_gates: ["drupal_bootstrap", "browser_flow"],
        advisory_gates: ["phpunit"],
      },
      merge_policy: {
        enabled: false,
        strategy: "squash",
        required_checks: [],
        delete_branch: true,
        target_branch: "main",
      },
      run_limits: {
        max_concurrent_runs: 1,
        daily_run_budget: 10,
      },
    })
    mockApi.saveWorkspaceDeliveryPolicy.mockImplementation((_projectId, body) =>
      Promise.resolve({
        project_id: "sports-cms",
        active: body.active,
        kill_switch: body.kill_switch,
        gate_policy: {
          agent_review: body.agent_review,
          qa_evidence: body.qa_evidence,
          blocking_gates: body.blocking_gates,
          advisory_gates: body.advisory_gates,
        },
        merge_policy: {
          enabled: body.merge_enabled,
          strategy: body.merge_strategy,
          required_checks: body.required_checks,
          delete_branch: body.delete_branch,
          target_branch: body.target_branch,
        },
        run_limits: {
          max_concurrent_runs: body.max_concurrent_runs,
          daily_run_budget: body.daily_run_budget,
        },
      })
    )
  })

  it("renders review gates, run limits, and disabled Auto-Merge", async () => {
    renderWithQueryClient(
      <WorkspaceDeliveryPolicySection projectId="sports-cms" />
    )

    expect(
      await screen.findByRole("heading", { name: "Delivery Policy" })
    ).not.toBeNull()
    await waitFor(() =>
      expect(screen.getByLabelText("Blocking gates")).toHaveProperty(
        "value",
        "drupal_bootstrap, browser_flow"
      )
    )
    expect(screen.getByLabelText("Advisory gates")).toHaveProperty(
      "value",
      "phpunit"
    )
    expect(screen.getByLabelText("Max concurrent runs")).toHaveProperty(
      "value",
      "1"
    )
    expect(screen.getByLabelText("Daily run budget")).toHaveProperty(
      "value",
      "10"
    )
    expect(
      screen.getByLabelText("Enable policy-gated Auto-Merge")
    ).toHaveProperty("checked", false)
  })

  it("saves policy-gated Auto-Merge and QA policy settings", async () => {
    renderWithQueryClient(
      <WorkspaceDeliveryPolicySection projectId="sports-cms" />
    )

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save policy" })
      ).toHaveProperty("disabled", false)
    )
    fireEvent.click(screen.getByLabelText("Enable policy-gated Auto-Merge"))
    fireEvent.change(screen.getByLabelText("Required merge checks"), {
      target: { value: "tests, lint" },
    })
    fireEvent.change(screen.getByLabelText("Daily run budget"), {
      target: { value: "20" },
    })
    fireEvent.change(screen.getByLabelText("Blocking gates"), {
      target: { value: "drupal_bootstrap, browser_flow, screenshot" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save policy" }))

    await waitFor(() =>
      expect(mockApi.saveWorkspaceDeliveryPolicy).toHaveBeenCalledWith(
        "sports-cms",
        {
          active: true,
          kill_switch: false,
          agent_review: true,
          qa_evidence: true,
          blocking_gates: ["drupal_bootstrap", "browser_flow", "screenshot"],
          advisory_gates: ["phpunit"],
          max_concurrent_runs: 1,
          daily_run_budget: 20,
          merge_enabled: true,
          merge_strategy: "squash",
          required_checks: ["tests", "lint"],
          delete_branch: true,
          target_branch: "main",
        }
      )
    )
  })
})
