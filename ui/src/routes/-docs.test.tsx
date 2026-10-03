/** @vitest-environment jsdom */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"
import { DocsTargetSection } from "@/components/DocsTargetSection"
import { api, type DocsSettings } from "@/lib/api"

vi.mock("@/lib/session", () => ({
  useSession: () => ({
    isLoading: false,
    data: { is_admin: true, login: "admin" },
  }),
}))
vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SettingsSection: ({ children }: { children: React.ReactNode }) => (
    <section>{children}</section>
  ),
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})
const saved: DocsSettings = {
  enabled: false,
  docs_repository: "org/docs",
  docs_base_branch: "main",
  docs_mcp_url: "",
  source_repositories: [],
  revision: "v1",
}

it("saves docs configuration and rolls back optimistic settings on failure", async () => {
  vi.spyOn(api, "getDocsSettings").mockResolvedValue(saved)
  const write = vi
    .spyOn(api, "saveDocsSettings")
    .mockRejectedValue(new Error("GitHub access unavailable"))
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <DocsTargetSection />
    </QueryClientProvider>
  )
  await screen.findByLabelText("Docs repository")
  fireEvent.change(screen.getByLabelText("Docs repository"), {
    target: { value: "org/new-docs" },
  })
  fireEvent.click(screen.getByText("Save settings"))
  await waitFor(() =>
    expect(write).toHaveBeenCalledWith(
      { ...saved, enabled: true, docs_repository: "org/new-docs" },
      expect.anything()
    )
  )
  await waitFor(() =>
    expect(client.getQueryData(["docsSettings"])).toEqual(saved)
  )
})
