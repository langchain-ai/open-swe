/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  parseGitHubPullRequestUrl,
  PreviewablePullRequestLink,
  PullRequestPreviewProvider,
} from "./PullRequestPreview"
import { api } from "@/lib/api"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it.each([
  { withProvider: false, state: "open", draft: false, expectedState: "open" },
  { withProvider: true, state: "closed", draft: true, expectedState: "closed" },
  { withProvider: false, state: "open", draft: true, expectedState: "draft" },
])(
  "loads metadata-only previews: $state, draft=$draft, provider=$withProvider",
  async ({ withProvider, state, draft, expectedState }) => {
    const preview = vi.spyOn(api, "getPullRequestPreview").mockResolvedValue({
      title: "Linked pull request",
      body: "",
      author: "alice",
      author_avatar_url: null,
      state,
      draft,
      head_ref: "feature",
      base_ref: "main",
      commits: 1,
      additions: 12,
      deletions: 3,
      changed_files: 2,
      files: [],
      unresolved: [],
      checks: [],
      human_input: "",
    })
    const linkContent = (
      <PreviewablePullRequestLink href="https://github.com/langchain-ai/langchainplus/pull/42">
        Related PR
      </PreviewablePullRequestLink>
    )
    const router = createRouter({
      routeTree: createRootRoute({
        component: () =>
          withProvider ? (
            <PullRequestPreviewProvider
              pullRequests={[]}
              healthUnavailable={false}
            >
              {linkContent}
            </PullRequestPreviewProvider>
          ) : (
            linkContent
          ),
      }),
      history: createMemoryHistory({ initialEntries: ["/"] }),
    })
    await router.load()
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    )
    expect(preview).not.toHaveBeenCalled()
    const link = screen.getByRole("link", { name: /Related PR/ })
    fireEvent.pointerEnter(link, { pointerType: "mouse" })
    await waitFor(() =>
      expect(screen.getByText("Linked pull request")).toBeTruthy()
    )
    expect(screen.getByText(expectedState)).toBeTruthy()
    expect(screen.getByText("feature")).toBeTruthy()
    expect(screen.queryByText("Loading PR health…")).toBeNull()
    expect(screen.queryByText(/GitHub health.*unavailable/)).toBeNull()
    expect(link.getAttribute("href")).toBe(
      "/agents/reviews/langchain-ai/langchainplus/42"
    )
  }
)
describe("parseGitHubPullRequestUrl", () => {
  it("parses GitHub pull request links anywhere in text renderers", () => {
    expect(
      parseGitHubPullRequestUrl(
        "https://github.com/langchain-ai/open-swe/pull/123?diff=split#discussion"
      )
    ).toEqual({ repoFullName: "langchain-ai/open-swe", number: 123 })
    expect(
      parseGitHubPullRequestUrl("https://www.github.com/org/repo/pull/7/")
    ).toEqual({ repoFullName: "org/repo", number: 7 })
  })

  it("ignores non-PR and non-GitHub links", () => {
    expect(
      parseGitHubPullRequestUrl("https://github.com/org/repo/issues/123")
    ).toBeNull()
    expect(
      parseGitHubPullRequestUrl("https://example.com/org/repo/pull/123")
    ).toBeNull()
  })
})
