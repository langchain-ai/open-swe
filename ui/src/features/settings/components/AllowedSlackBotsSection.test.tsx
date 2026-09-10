/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AllowedSlackBotsSection } from "./AllowedSlackBotsSection"

const BOT = {
  bot_id: "B123",
  user_id: "U123",
  team_id: "T123",
  app_id: "A123",
  name: "Release bot",
  github_login: "alice",
  created_at: "2026-09-09",
}
const clients: Array<QueryClient> = []

afterEach(() => {
  cleanup()
  for (const client of clients) client.clear()
  clients.length = 0
  vi.unstubAllGlobals()
})

function renderSection(isAdmin = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <AllowedSlackBotsSection isAdmin={isAdmin} />
    </QueryClientProvider>
  )
}

describe("Allowed Slack bots", () => {
  it("lets an admin add a bot, see its owner, and remove it", async () => {
    let bots: Array<typeof BOT> = []
    const requests: Array<{ url: string; init: RequestInit }> = []
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        requests.push({ url, init })
        if (init.method === "POST") {
          bots = [BOT]
          return new Response(JSON.stringify(BOT))
        }
        if (init.method === "DELETE") {
          bots = []
          return new Response(JSON.stringify({ ok: true }))
        }
        return new Response(JSON.stringify(bots))
      })
    )
    renderSection()
    await screen.findByText("No Slack bots are allowed.")
    fireEvent.change(screen.getByLabelText("Slack bot ID"), {
      target: { value: " U123 " },
    })
    fireEvent.click(screen.getByRole("button", { name: "Allow bot" }))
    expect(await screen.findByText("Release bot")).toBeTruthy()
    expect(screen.getByText(/Runs as alice/)).toBeTruthy()
    expect(requests.find(({ init }) => init.method === "POST")?.init.body).toBe(
      JSON.stringify({ bot_id: "U123" })
    )
    fireEvent.click(screen.getByRole("button", { name: "Remove Release bot" }))
    await screen.findByText("No Slack bots are allowed.")
    expect(
      requests.find(({ init }) => init.method === "DELETE")?.url
    ).toContain("/slack/allowed-bots/T123/B123")
  })

  it("shows a failed add without claiming the bot is allowed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit) =>
        init.method === "POST"
          ? new Response(
              JSON.stringify({ detail: "That Slack member is not a bot." }),
              { status: 400 }
            )
          : new Response("[]")
      )
    )
    renderSection()
    await screen.findByText("No Slack bots are allowed.")
    fireEvent.change(screen.getByLabelText("Slack bot ID"), {
      target: { value: "UHUMAN" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Allow bot" }))
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "That Slack member is not a bot."
    )
    expect(screen.getByText("No Slack bots are allowed.")).toBeTruthy()
  })

  it("does not show controls or load the allowlist for non-admins", async () => {
    const fetch = vi.fn()
    vi.stubGlobal("fetch", fetch)
    const view = renderSection(false)
    await waitFor(() => expect(view.container.innerHTML).toBe(""))
    expect(fetch).not.toHaveBeenCalled()
  })
})
