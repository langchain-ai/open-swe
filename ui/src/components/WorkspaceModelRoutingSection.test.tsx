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

import { WorkspaceModelRoutingSection } from "./WorkspaceModelRoutingSection"

const mockApi = vi.hoisted(() => ({
  getWorkspaceModelRouting: vi.fn(),
  saveWorkspaceModelRouting: vi.fn(),
}))

vi.mock("@/lib/api", () => ({
  api: mockApi,
}))

function payload() {
  return {
    project_id: "sports-cms",
    environment: "default",
    roles: ["executor", "qa", "drupal_backend", "helper", "fallback"],
    routing: {
      environment: "default",
      roles: {
        executor: {
          endpoint_id: "ai-hub-main",
          model_id: "gpt-5.5",
          effort: "high",
          capabilities: { tool_calling: true },
        },
      },
      fallback: {
        endpoint_id: "ai-hub-main",
        model_id: "gpt-5.5",
        effort: "medium",
      },
    },
    endpoints: [
      {
        id: "ai-hub-main",
        display_name: "AI Hub",
        provider_type: "ai_hub",
        disabled: false,
        base_url_fingerprint: "abc123",
        models: [
          {
            model_id: "gpt-5.5",
            capabilities: { tool_calling: true, context_window: 128000 },
          },
        ],
        supports_model_discovery: true,
      },
      {
        id: "custom-main",
        display_name: "Custom",
        provider_type: "openai_compatible",
        disabled: false,
        base_url_fingerprint: "def456",
        models: [{ model_id: "custom-large", capabilities: { vision: true } }],
        supports_model_discovery: false,
      },
    ],
    legacy_models: [],
    validation: { ready: true, blockers: [] },
  }
}

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

describe("WorkspaceModelRoutingSection", () => {
  afterEach(() => cleanup())

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getWorkspaceModelRouting.mockResolvedValue(payload())
    mockApi.saveWorkspaceModelRouting.mockImplementation((_projectId, body) =>
      Promise.resolve({ ...payload(), routing: body, validation: { ready: true, blockers: [] } })
    )
  })

  it("groups model choices by endpoint and saves role selections", async () => {
    renderWithQueryClient(<WorkspaceModelRoutingSection projectId="sports-cms" />)

    expect(
      await screen.findByRole("heading", { name: "Model Routing" })
    ).not.toBeNull()
    await waitFor(() =>
      expect(screen.getByLabelText("executor endpoint")).toHaveProperty(
        "value",
        "ai-hub-main"
      )
    )
    fireEvent.change(screen.getByLabelText("qa endpoint"), {
      target: { value: "custom-main" },
    })
    expect(screen.getByLabelText("qa model")).toHaveProperty(
      "value",
      "custom-large"
    )
    fireEvent.click(screen.getByLabelText("qa vision"))
    fireEvent.change(screen.getByLabelText("qa context window"), {
      target: { value: "32000" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save routing" }))

    await waitFor(() =>
      expect(mockApi.saveWorkspaceModelRouting).toHaveBeenCalledWith(
        "sports-cms",
        expect.objectContaining({
          environment: "default",
          roles: expect.objectContaining({
            qa: expect.objectContaining({
              endpoint_id: "custom-main",
              model_id: "custom-large",
              capabilities: expect.objectContaining({
                vision: true,
                context_window: 32000,
              }),
            }),
          }),
        })
      )
    )
  })

  it("displays routing validation blockers", async () => {
    mockApi.getWorkspaceModelRouting.mockResolvedValueOnce({
      ...payload(),
      validation: {
        ready: false,
        blockers: [{ message: "Project secret CUSTOM_MODEL_API_KEY is missing." }],
      },
    })

    renderWithQueryClient(<WorkspaceModelRoutingSection projectId="sports-cms" />)

    expect(
      await screen.findByText("Project secret CUSTOM_MODEL_API_KEY is missing.")
    ).not.toBeNull()
    expect(screen.getByText("Blocked")).not.toBeNull()
  })
})
