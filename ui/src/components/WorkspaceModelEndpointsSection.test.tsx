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

import { WorkspaceModelEndpointsSection } from "./WorkspaceModelEndpointsSection"

const mockApi = vi.hoisted(() => ({
  listModelEndpointPresets: vi.fn(),
  listModelEndpoints: vi.fn(),
  createModelEndpointPreset: vi.fn(),
  saveModelEndpoint: vi.fn(),
  validateModelEndpoint: vi.fn(),
  deleteModelEndpoint: vi.fn(),
}))

vi.mock("@/lib/api", () => ({
  api: mockApi,
}))

function endpoint(overrides = {}) {
  return {
    id: "deepseek-main",
    display_name: "DeepSeek",
    provider_type: "deepseek",
    base_url: "https://api.deepseek.com/v1",
    api_path: "/chat/completions",
    auth_type: "bearer",
    secret_name: "DEEPSEEK_API_KEY",
    default_headers: ["X-Team"],
    model_ids: ["deepseek-chat", "deepseek-reasoner"],
    organization: "",
    project: "",
    timeout_seconds: 60,
    rate_limit: {},
    supports_model_discovery: true,
    disabled: false,
    secret: {
      name: "DEEPSEEK_API_KEY",
      connected: false,
      environment: "default",
    },
    ...overrides,
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

describe("WorkspaceModelEndpointsSection", () => {
  afterEach(() => cleanup())

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.listModelEndpointPresets.mockResolvedValue({
      items: [
        {
          display_name: "AI Hub",
          provider_type: "ai_hub",
          base_url: "https://api.openai.com/v1",
          api_path: "/chat/completions",
          auth_type: "bearer",
          secret_name: "AI_HUB_API_KEY",
          model_ids: [],
          supports_model_discovery: true,
        },
        {
          display_name: "DeepSeek",
          provider_type: "deepseek",
          base_url: "https://api.deepseek.com/v1",
          api_path: "/chat/completions",
          auth_type: "bearer",
          secret_name: "DEEPSEEK_API_KEY",
          model_ids: ["deepseek-chat"],
          supports_model_discovery: true,
        },
      ],
    })
    mockApi.listModelEndpoints.mockResolvedValue({
      project_id: "sports-cms",
      environment: "default",
      items: [endpoint()],
    })
    mockApi.createModelEndpointPreset.mockResolvedValue(endpoint())
    mockApi.saveModelEndpoint.mockImplementation(
      (_projectId, _endpointId, _environment, body) =>
        Promise.resolve(
          endpoint({
            ...body,
            default_headers: Object.keys(body.default_headers),
            secret: {
              name: body.secret_name,
              connected: false,
              environment: "default",
            },
          })
        )
    )
    mockApi.validateModelEndpoint.mockResolvedValue({
      ready: false,
      project_id: "sports-cms",
      environment: "default",
      id: "deepseek-main",
      blockers: [
        {
          code: "missing_secret",
          message: "Project secret DEEPSEEK_API_KEY is missing.",
        },
      ],
      models: ["deepseek-chat"],
      model_discovery: true,
    })
    mockApi.deleteModelEndpoint.mockResolvedValue({
      deleted: true,
      id: "deepseek-main",
    })
  })

  it("renders redacted endpoint status and does not expose secret values", async () => {
    renderWithQueryClient(
      <WorkspaceModelEndpointsSection projectId="sports-cms" />
    )

    expect(
      await screen.findByRole("heading", { name: "Model Endpoints" })
    ).not.toBeNull()
    expect(await screen.findByText("Secret missing")).not.toBeNull()
    expect(screen.getByLabelText("deepseek-main secret reference")).toHaveProperty(
      "value",
      "DEEPSEEK_API_KEY"
    )
    expect(screen.queryByText("deepseek-secret-1234")).toBeNull()
  })

  it("creates endpoint presets for the selected provider", async () => {
    renderWithQueryClient(
      <WorkspaceModelEndpointsSection projectId="sports-cms" />
    )

    await waitFor(() =>
      expect(screen.getByLabelText("Model endpoint preset")).toHaveProperty(
        "value",
        "ai_hub"
      )
    )
    fireEvent.change(screen.getByLabelText("Model endpoint preset"), {
      target: { value: "deepseek" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Add preset" }))

    await waitFor(() =>
      expect(mockApi.createModelEndpointPreset).toHaveBeenCalledWith(
        "sports-cms",
        "deepseek",
        "default"
      )
    )
  })

  it("validates endpoints and displays actionable blockers", async () => {
    renderWithQueryClient(
      <WorkspaceModelEndpointsSection projectId="sports-cms" />
    )

    await screen.findByRole("button", { name: "Validate" })
    fireEvent.click(screen.getByRole("button", { name: "Validate" }))

    expect(
      await screen.findByText("Project secret DEEPSEEK_API_KEY is missing.")
    ).not.toBeNull()
  })

  it("saves, disables, and removes endpoints", async () => {
    renderWithQueryClient(
      <WorkspaceModelEndpointsSection projectId="sports-cms" />
    )

    await waitFor(() =>
      expect(screen.getByLabelText("deepseek-main model IDs")).toHaveProperty(
        "value",
        "deepseek-chat, deepseek-reasoner"
      )
    )
    fireEvent.change(screen.getByLabelText("deepseek-main model IDs"), {
      target: { value: "deepseek-chat, deepseek-coder" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save endpoint" }))

    await waitFor(() =>
      expect(mockApi.saveModelEndpoint).toHaveBeenCalledWith(
        "sports-cms",
        "deepseek-main",
        "default",
        expect.objectContaining({
          model_ids: ["deepseek-chat", "deepseek-coder"],
        })
      )
    )

    fireEvent.click(screen.getByRole("button", { name: "Disable" }))
    await waitFor(() =>
      expect(mockApi.saveModelEndpoint).toHaveBeenCalledWith(
        "sports-cms",
        "deepseek-main",
        "default",
        expect.objectContaining({ disabled: true })
      )
    )

    fireEvent.click(screen.getByRole("button", { name: "Remove" }))
    await waitFor(() =>
      expect(mockApi.deleteModelEndpoint).toHaveBeenCalledWith(
        "sports-cms",
        "deepseek-main",
        "default"
      )
    )
  })
})
