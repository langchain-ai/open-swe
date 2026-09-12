/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AllowedSlackBotsSection } from "./AllowedSlackBotsSection"

const BOT = {
  bot_id: "B123",
  user_id: "U123",
  team_id: "T123",
  app_id: "A123",
  name: "Release bot",
  created_by: "alice",
  created_at: "2026-09-09",
}
const DIRECTORY = [
  { ...BOT, image_url: "https://avatars.slack-edge.com/release.png" },
  { ...BOT, bot_id: "B456", user_id: "U456", name: "Build bot", image_url: "" },
]
const clients: Array<QueryClient> = []

afterEach(() => {
  cleanup()
  for (const client of clients) client.clear()
  clients.length = 0
  vi.unstubAllGlobals()
})

function renderSection() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <AllowedSlackBotsSection />
    </QueryClientProvider>
  )
}

describe("Allowed Slack bots", () => {
  it("lets an admin browse, allow, and remove a bot", async () => {
    let bots: Array<typeof BOT> = []
    const requests: Array<{ url: string; init: RequestInit }> = []
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        requests.push({ url, init })
        if (url.endsWith("/slack/bots"))
          return new Response(JSON.stringify(DIRECTORY))
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
    expect(
      screen.queryByRole("textbox", { name: "Search Slack bots" })
    ).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Add bot" }))
    const search = await screen.findByRole("textbox", {
      name: "Search Slack bots",
    })
    fireEvent.change(search, { target: { value: "Release" } })
    expect(screen.queryByRole("button", { name: "Allow Build bot" })).toBeNull()
    fireEvent.click(await screen.findByText("Release bot"))
    await screen.findByRole("button", { name: "Remove Release bot" })
    expect(await screen.findByText("Release bot")).toBeTruthy()
    expect(
      screen.queryByRole("textbox", { name: "Search Slack bots" })
    ).toBeNull()
    expect(requests.find(({ init }) => init.method === "POST")?.init.body).toBe(
      JSON.stringify({ bot_id: "U123" })
    )
    fireEvent.click(screen.getByRole("button", { name: "Add bot" }))
    expect(
      await screen.findByRole("button", {
        name: "Release bot is already allowed",
      })
    ).toHaveProperty("disabled", true)
    expect(
      screen.getByRole("button", { name: "Allow Build bot" })
    ).toHaveProperty("disabled", false)
    fireEvent.click(screen.getByRole("button", { name: "Add bot" }))
    fireEvent.click(screen.getByRole("button", { name: "Remove Release bot" }))
    await screen.findByText("No Slack bots are allowed.")
    expect(
      requests.find(({ init }) => init.method === "DELETE")?.url
    ).toContain("/slack/allowed-bots/T123/B123")
  })

  it("supports manual entry when browsing fails and reports rejected additions", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) =>
        url.endsWith("/slack/bots")
          ? new Response(
              JSON.stringify({ detail: "Slack is temporarily unavailable." }),
              { status: 502 }
            )
          : init.method === "POST"
            ? new Response(
                JSON.stringify({ detail: "That Slack member is not a bot." }),
                { status: 400 }
              )
            : new Response("[]")
      )
    )
    renderSection()
    await screen.findByText("No Slack bots are allowed.")
    fireEvent.click(screen.getByRole("button", { name: "Add bot" }))
    await screen.findByText("Slack is temporarily unavailable.")
    fireEvent.click(
      screen.getByRole("button", { name: "Enter bot ID manually" })
    )
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
})
