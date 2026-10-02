/** @vitest-environment jsdom */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { Route } from "./docs";
import { api, type DocsSettings } from "@/lib/api";

vi.mock("@/lib/session", () => ({
  useSession: () => ({
    isLoading: false,
    data: { is_admin: true, login: "admin" },
  }),
}));
vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SettingsSection: ({ children }: { children: React.ReactNode }) => (
    <section>{children}</section>
  ),
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const saved: DocsSettings = {
  enabled: false,
  docs_repository: "org/docs",
  docs_base_branch: "main",
  docs_mcp_url: "",
  source_repositories: [],
  revision: "v1",
};

it("saves docs configuration and rolls back optimistic settings on failure", async () => {
  vi.spyOn(api, "getDocsSettings").mockResolvedValue(saved);
  const write = vi
    .spyOn(api, "saveDocsSettings")
    .mockRejectedValue(new Error("GitHub access unavailable"));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Component = Route.options.component;
  if (!Component) throw new Error("missing docs page");
  render(
    <QueryClientProvider client={client}>
      <Component />
    </QueryClientProvider>,
  );
  await screen.findByLabelText("Docs repository");
  fireEvent.change(screen.getByLabelText("Source repositories"), {
    target: { value: "org/app\norg/sdk" },
  });
  fireEvent.click(screen.getByText("Save settings"));
  await waitFor(() =>
    expect(write).toHaveBeenCalledWith(
      { ...saved, source_repositories: ["org/app", "org/sdk"] },
      expect.anything(),
    ),
  );
  await waitFor(() =>
    expect(client.getQueryData(["docsSettings"])).toEqual(saved),
  );
});
