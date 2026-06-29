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

import { WorkspaceRepositoriesSection } from "./WorkspaceRepositoriesSection"

const mockApi = vi.hoisted(() => ({
  getWorkspaceRepositories: vi.fn(),
  saveWorkspaceRepositories: vi.fn(),
  testWorkspaceRepositoryAccess: vi.fn(),
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

describe("WorkspaceRepositoriesSection", () => {
  afterEach(() => cleanup())

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getWorkspaceRepositories.mockResolvedValue({
      provider: "github",
      repositories: ["example/sports-cms"],
      default_repository: "example/sports-cms",
      branch_policy: {
        base_branch: "main",
        branch_prefix: "delivery/sports-cms",
        draft_pull_requests: true,
      },
      credential_policy: {
        provider: "github",
        requires_user_pat: true,
        allowed_actions: ["branch", "commit", "pull_request"],
      },
      context_pack: {
        repositories: ["example/sports-cms"],
        required_documents: ["README.md"],
      },
      access: [
        {
          full_name: "example/sports-cms",
          default: true,
          status: "blocked",
          message: "github token unavailable, re-login required",
        },
      ],
    })
    mockApi.saveWorkspaceRepositories.mockImplementation((_projectId, body) =>
      Promise.resolve({
        provider: "github",
        repositories: body.repositories.includes(body.default_repository)
          ? body.repositories
          : [...body.repositories, body.default_repository],
        default_repository: body.default_repository,
        branch_policy: {
          base_branch: body.base_branch,
          branch_prefix: body.branch_prefix,
          draft_pull_requests: body.draft_pull_requests,
        },
        credential_policy: {
          provider: "github",
          requires_user_pat: true,
          allowed_actions: body.allowed_actions,
        },
        context_pack: {
          repositories: body.context_repositories,
          required_documents: body.required_documents,
        },
        access: [],
      })
    )
    mockApi.testWorkspaceRepositoryAccess.mockResolvedValue({
      provider: "github",
      repositories: ["example/sports-cms"],
      default_repository: "example/sports-cms",
      branch_policy: {
        base_branch: "main",
        branch_prefix: "delivery/sports-cms",
        draft_pull_requests: true,
      },
      credential_policy: {
        provider: "github",
        requires_user_pat: true,
        allowed_actions: ["branch", "commit", "pull_request"],
      },
      context_pack: {
        repositories: ["example/sports-cms"],
        required_documents: ["README.md"],
      },
      access: [
        {
          full_name: "example/sports-cms",
          default: true,
          status: "ready",
          message: "Repository access verified.",
        },
      ],
    })
  })

  it("renders configured repositories and blocked access status", async () => {
    renderWithQueryClient(
      <WorkspaceRepositoriesSection projectId="sports-cms" />
    )

    expect(
      await screen.findByRole("heading", { name: "Repositories" })
    ).not.toBeNull()
    await waitFor(() =>
      expect(screen.getByLabelText("Workspace repositories")).toHaveProperty(
        "value",
        "example/sports-cms"
      )
    )
    expect(screen.getByLabelText("Default repository")).toHaveProperty(
      "value",
      "example/sports-cms"
    )
    expect(
      await screen.findByText("github token unavailable, re-login required")
    ).not.toBeNull()
  })

  it("saves repository policy and context pack settings", async () => {
    renderWithQueryClient(
      <WorkspaceRepositoriesSection projectId="sports-cms" />
    )

    await waitFor(() =>
      expect(screen.getByLabelText("Workspace repositories")).toHaveProperty(
        "value",
        "example/sports-cms"
      )
    )
    fireEvent.change(screen.getByLabelText("Workspace repositories"), {
      target: { value: "example/sports-cms, example/theme-kit" },
    })
    fireEvent.change(screen.getByLabelText("Default repository"), {
      target: { value: "example/theme-kit" },
    })
    fireEvent.change(screen.getByLabelText("Base branch"), {
      target: { value: "develop" },
    })
    fireEvent.change(screen.getByLabelText("Required documents"), {
      target: { value: "README.md, docs/gates.md" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(mockApi.saveWorkspaceRepositories).toHaveBeenCalledWith(
        "sports-cms",
        {
          provider: "github",
          repositories: ["example/sports-cms", "example/theme-kit"],
          default_repository: "example/theme-kit",
          base_branch: "develop",
          branch_prefix: "delivery/sports-cms",
          draft_pull_requests: true,
          allowed_actions: ["branch", "commit", "pull_request"],
          context_repositories: ["example/sports-cms"],
          required_documents: ["README.md", "docs/gates.md"],
        }
      )
    )
  })

  it("runs access test and displays ready state", async () => {
    renderWithQueryClient(
      <WorkspaceRepositoriesSection projectId="sports-cms" />
    )

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Test access" })).toHaveProperty(
        "disabled",
        false
      )
    )
    fireEvent.click(screen.getByRole("button", { name: "Test access" }))

    expect(await screen.findByText("Repository access verified.")).not.toBeNull()
  })

  it("shows API validation errors", async () => {
    mockApi.saveWorkspaceRepositories.mockRejectedValueOnce(
      new Error("default repository must be owner/repo")
    )
    renderWithQueryClient(
      <WorkspaceRepositoriesSection projectId="sports-cms" />
    )

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save" })).toHaveProperty(
        "disabled",
        false
      )
    )
    fireEvent.change(screen.getByLabelText("Default repository"), {
      target: { value: "invalid" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    expect(
      await screen.findByText("default repository must be owner/repo")
    ).not.toBeNull()
  })
})
