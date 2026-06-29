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
import { WorkspaceCredentialsSection } from "./WorkspaceCredentialsSection"
import type { ReactNode } from "react"


const mockApi = vi.hoisted(() => ({
  listProjectSecrets: vi.fn(),
  getProjectAIHubReadiness: vi.fn(),
  getProjectAIHubImportShape: vi.fn(),
  saveProjectSecret: vi.fn(),
  testProjectSecret: vi.fn(),
  revokeProjectSecret: vi.fn(),
  importProjectAIHubSecrets: vi.fn(),
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

describe("WorkspaceCredentialsSection", () => {
  afterEach(() => cleanup())

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.listProjectSecrets.mockResolvedValue({
      items: [
        {
          connected: true,
          project_id: "sports-cms",
          environment: "default",
          name: "AI_HUB_API_KEY",
          kind: "ai_hub_credential",
          value_last4: "6789",
          version: 2,
          updated_at: "2026-06-28T10:00:00Z",
        },
      ],
    })
    mockApi.getProjectAIHubReadiness.mockResolvedValue({
      ready: false,
      environment: "default",
      blockers: [
        {
          code: "missing_ai_hub_base_url",
          message: "AI Hub base URL is missing.",
        },
      ],
    })
    mockApi.getProjectAIHubImportShape.mockResolvedValue({
      provider: "ai_hub",
      candidates: [
        {
          prefix: "AI_HUB",
          required_secrets: [
            {
              name: "AI_HUB_BASE_URL",
              source_env: "AI_HUB_BASE_URL",
              present: true,
            },
            {
              name: "AI_HUB_API_KEY",
              source_env: "AI_HUB_API_KEY",
              present: false,
            },
          ],
          model_list_env: "AI_HUB_MODELS",
          model_list_present: true,
        },
      ],
    })
    mockApi.saveProjectSecret.mockImplementation((projectId, name, body) =>
      Promise.resolve({
        connected: true,
        project_id: projectId,
        environment: body.environment,
        name,
        value_last4: body.value.slice(-4),
        version: 1,
        updated_at: "2026-06-28T11:00:00Z",
      })
    )
    mockApi.testProjectSecret.mockImplementation((projectId, name, body) =>
      Promise.resolve({
        ready: true,
        project_id: projectId,
        environment: body.environment,
        name,
      })
    )
    mockApi.revokeProjectSecret.mockImplementation(
      (projectId, name, environment) =>
        Promise.resolve({
          connected: false,
          project_id: projectId,
          environment,
          name,
        })
    )
    mockApi.importProjectAIHubSecrets.mockResolvedValue({
      provider: "ai_hub",
      project_id: "sports-cms",
      environment: "default",
      source_prefix: "AI_HUB",
      imported: [
        {
          connected: true,
          project_id: "sports-cms",
          environment: "default",
          name: "AI_HUB_BASE_URL",
          value_last4: "/v1",
        },
        {
          connected: true,
          project_id: "sports-cms",
          environment: "default",
          name: "AI_HUB_API_KEY",
          value_last4: "9999",
        },
      ],
      shape: { provider: "ai_hub", candidates: [] },
    })
  })

  it("shows AI Hub readiness, redacted status, and import shape without secret values", async () => {
    renderWithQueryClient(
      <WorkspaceCredentialsSection projectId="sports-cms" />
    )

    expect(await screen.findByText("AI Hub readiness")).not.toBeNull()
    const providerTokens = await screen.findByRole("link", {
      name: "Open Profile Settings",
    })
    expect(providerTokens.getAttribute("href")).toBe("/my-settings")
    expect(
      await screen.findByText("AI Hub base URL is missing.")
    ).not.toBeNull()
    expect(await screen.findByText(/value ••••6789/)).not.toBeNull()
    expect(await screen.findByText(/AI_HUB_API_KEY: missing/)).not.toBeNull()
    expect(document.body.textContent).not.toContain("aihub-secret-6789")
  })

  it("saves missing secrets and rotates connected secrets without rendering values", async () => {
    renderWithQueryClient(
      <WorkspaceCredentialsSection projectId="sports-cms" />
    )

    expect(await screen.findByText(/value ••••6789/)).not.toBeNull()
    const baseUrl = screen.getByLabelText("AI_HUB_BASE_URL value")
    fireEvent.change(baseUrl, {
      target: { value: "https://ai-hub.example/v1" },
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Save AI_HUB_BASE_URL" })
    )

    await waitFor(() =>
      expect(mockApi.saveProjectSecret).toHaveBeenCalledWith(
        "sports-cms",
        "AI_HUB_BASE_URL",
        {
          environment: "default",
          value: "https://ai-hub.example/v1",
          kind: "ai_hub_credential",
        }
      )
    )
    expect(document.body.textContent).not.toContain("https://ai-hub.example/v1")

    const apiKey = screen.getByLabelText("AI_HUB_API_KEY value")
    fireEvent.change(apiKey, { target: { value: "aihub-secret-9999" } })
    fireEvent.click(
      screen.getByRole("button", { name: "Rotate AI_HUB_API_KEY" })
    )

    await waitFor(() =>
      expect(mockApi.saveProjectSecret).toHaveBeenCalledWith(
        "sports-cms",
        "AI_HUB_API_KEY",
        {
          environment: "default",
          value: "aihub-secret-9999",
          kind: "ai_hub_credential",
        }
      )
    )
    expect(document.body.textContent).not.toContain("aihub-secret-9999")
  })

  it("tests, imports, revokes, and adds custom project secrets", async () => {
    renderWithQueryClient(
      <WorkspaceCredentialsSection projectId="sports-cms" />
    )

    expect(await screen.findByText(/value ••••6789/)).not.toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Test AI_HUB_API_KEY" }))
    expect(await screen.findByText("Validation passed.")).not.toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Import AI Hub" }))
    expect(
      await screen.findByText(/Imported 2 secrets from AI_HUB/)
    ).not.toBeNull()
    expect(document.body.textContent).not.toContain("aihub-secret")

    fireEvent.click(
      screen.getByRole("button", { name: "Revoke AI_HUB_API_KEY" })
    )
    await waitFor(() =>
      expect(mockApi.revokeProjectSecret).toHaveBeenCalledWith(
        "sports-cms",
        "AI_HUB_API_KEY",
        "default"
      )
    )

    fireEvent.change(screen.getByLabelText("Custom secret name"), {
      target: { value: "DRUPAL_API_KEY" },
    })
    fireEvent.change(screen.getByLabelText("Custom secret value"), {
      target: { value: "drupal-secret-1357" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Add" }))

    await waitFor(() =>
      expect(mockApi.saveProjectSecret).toHaveBeenCalledWith(
        "sports-cms",
        "DRUPAL_API_KEY",
        {
          environment: "default",
          value: "drupal-secret-1357",
          kind: "api_key",
        }
      )
    )
    expect(document.body.textContent).not.toContain("drupal-secret-1357")
  })

  it("shows failed validation and API error states", async () => {
    mockApi.testProjectSecret.mockResolvedValueOnce({
      ready: false,
      project_id: "sports-cms",
      environment: "default",
      name: "AI_HUB_API_KEY",
    })
    mockApi.saveProjectSecret.mockRejectedValueOnce(
      new Error("invalid project secret name")
    )

    renderWithQueryClient(
      <WorkspaceCredentialsSection projectId="sports-cms" />
    )

    expect(await screen.findByText(/value ••••6789/)).not.toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Test AI_HUB_API_KEY" }))
    expect(await screen.findByText("Secret is missing.")).not.toBeNull()

    fireEvent.change(screen.getByLabelText("Custom secret name"), {
      target: { value: "bad-name" },
    })
    fireEvent.change(screen.getByLabelText("Custom secret value"), {
      target: { value: "secret" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Add" }))

    expect(
      await screen.findByText("invalid project secret name")
    ).not.toBeNull()
  })
})
