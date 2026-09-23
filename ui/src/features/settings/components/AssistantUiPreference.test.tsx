// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { api } from "@/lib/api"
import { AssistantUiPreference } from "./AssistantUiPreference"

const session = vi.hoisted(() => ({ login: "alice" }))
vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { login: session.login } }),
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  session.login = "alice"
})

it("saves the account preference and isolates it when the account changes", async () => {
  vi.spyOn(api, "profile").mockImplementation(async () => ({
    login: session.login,
    default_model: "openai:test",
    reasoning_effort: "medium",
    draft_prs: false,
    experimental_assistant_ui: session.login === "alice",
  }))
  vi.spyOn(api, "options").mockResolvedValue({
    models: [],
    default_agent_model: "openai:test",
    default_agent_reasoning_effort: "medium",
    default_agent_subagent_model: "openai:test",
    default_agent_subagent_reasoning_effort: "medium",
  })
  const save = vi
    .spyOn(api, "saveProfile")
    .mockImplementation(async (body) => ({
      ...body,
      model_routing_enabled: body.model_routing_enabled ?? undefined,
    }))
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const view = () => (
    <QueryClientProvider client={client}>
      <AssistantUiPreference />
    </QueryClientProvider>
  )
  const rendered = render(view())
  const toggle = await screen.findByRole("switch", {
    name: /Assistant UI \(experimental\)/,
  })
  await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("true"))
  fireEvent.click(toggle)
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({
        experimental_assistant_ui: false,
        draft_prs: false,
        default_model: "openai:test",
      })
    )
  )
  await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("false"))
  fireEvent.click(toggle)
  await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("true"))
  session.login = "bob"
  rendered.rerender(view())
  await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("false"))
  expect(client.getQueryData(["profile", "alice"])).toMatchObject({
    experimental_assistant_ui: true,
  })
  expect(client.getQueryData(["profile", "bob"])).toMatchObject({
    experimental_assistant_ui: false,
  })
})
