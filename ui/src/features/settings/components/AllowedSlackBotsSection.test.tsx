/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  act,
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
  created_by: "alice",
  environment: "backend",
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

function renderSection(
  isAdmin = true,
  environments = [
    { slug: "backend", name: "Backend", repos: ["langchain-ai/open-swe"] },
  ]
) {
  const originalFetch = globalThis.fetch
  vi.stubGlobal("fetch", (url: string, init: RequestInit) =>
    url.endsWith("/environments/options")
      ? Promise.resolve(new Response(JSON.stringify({ environments })))
      : originalFetch(url, init)
  )
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

async function chooseEnvironment() {
  fireEvent.click(await screen.findByLabelText("Environment"))
  fireEvent.click(await screen.findByRole("option", { name: "Backend" }))
}

describe("Allowed Slack bots", () => {
  it("requires an environment before allowing a manually entered bot", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("[]"))
    )
    renderSection()
    await screen.findByText("No Slack bots are allowed.")
    fireEvent.click(
      screen.getByRole("button", { name: "Enter bot ID manually" })
    )
    fireEvent.change(screen.getByLabelText("Slack bot ID"), {
      target: { value: "B123" },
    })
    expect(screen.getByRole("button", { name: "Allow bot" })).toHaveProperty(
      "disabled",
      true
    )
    await chooseEnvironment()
    expect(screen.getByLabelText("Environment").textContent).toContain(
      "Backend"
    )
    expect(screen.getByRole("button", { name: "Allow bot" })).toHaveProperty(
      "disabled",
      false
    )
    expect(
      screen.getByText("GitHub access: langchain-ai/open-swe")
    ).toBeTruthy()
  })

  it("disables environments without repositories", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("[]"))
    )
    renderSection(true, [{ slug: "empty", name: "Empty", repos: [] }])
    await screen.findByText("No Slack bots are allowed.")
    fireEvent.click(await screen.findByLabelText("Environment"))
    expect(
      (
        await screen.findByRole("option", { name: "Empty (no repositories)" })
      ).getAttribute("aria-disabled")
    ).toBe("true")
  })

  it("lets an admin add a bot, choose its environment, and remove it", async () => {
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
    const allowButton = screen.getByRole("button", { name: "Allow bot" })
    const search = await screen.findByRole("combobox", {
      name: "Search Slack bots",
    })
    await waitFor(() => expect(search).toHaveProperty("disabled", false))
    act(() => search.focus())
    fireEvent.click(search)
    fireEvent.input(search, {
      target: { value: "Release" },
      inputType: "insertText",
      data: "Release",
    })
    fireEvent.click(await screen.findByRole("option", { name: /Release bot/ }))
    fireEvent.input(search, {
      target: { value: "Build" },
      inputType: "insertText",
      data: "Build",
    })
    expect(allowButton).toHaveProperty("disabled", true)
    fireEvent.input(search, {
      target: { value: "Release" },
      inputType: "insertText",
      data: "Release",
    })
    fireEvent.click(await screen.findByRole("option", { name: /Release bot/ }))
    await chooseEnvironment()
    fireEvent.click(screen.getByRole("button", { name: "Allow bot" }))
    expect(await screen.findByText("Release bot")).toBeTruthy()
    expect(screen.getByText(/Runs as Open SWE.*Backend/)).toBeTruthy()
    expect(requests.find(({ init }) => init.method === "POST")?.init.body).toBe(
      JSON.stringify({ bot_id: "U123", environment: "backend" })
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
    fireEvent.click(
      screen.getByRole("button", { name: "Enter bot ID manually" })
    )
    fireEvent.change(screen.getByLabelText("Slack bot ID"), {
      target: { value: "UHUMAN" },
    })
    await chooseEnvironment()
    fireEvent.click(screen.getByRole("button", { name: "Allow bot" }))
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "That Slack member is not a bot."
    )
    expect(screen.getByText("No Slack bots are allowed.")).toBeTruthy()
  })

  it("searches names and disables bots that are already allowed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (url: string) =>
          new Response(
            JSON.stringify(url.endsWith("/slack/bots") ? DIRECTORY : [BOT])
          )
      )
    )
    renderSection()
    const search = await screen.findByRole("combobox", {
      name: "Search Slack bots",
    })
    await waitFor(() => expect(search).toHaveProperty("disabled", false))
    act(() => search.focus())
    fireEvent.click(search)
    fireEvent.input(search, {
      target: { value: "Release" },
      inputType: "insertText",
      data: "Release",
    })
    const option = await screen.findByRole("option", {
      name: /Release bot.*Already allowed/,
    })
    expect(option.getAttribute("aria-disabled")).toBe("true")
    expect(screen.queryByRole("option", { name: /Build bot/ })).toBeNull()
    fireEvent.input(search, {
      target: { value: "Build" },
      inputType: "insertText",
      data: "Build",
    })
    fireEvent.click(await screen.findByRole("option", { name: /Build bot/ }))
    await chooseEnvironment()
    expect(screen.getByRole("button", { name: "Allow bot" })).toHaveProperty(
      "disabled",
      false
    )
  })

  it("keeps manual entry available when Slack browsing fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.endsWith("/slack/bots")
          ? new Response(
              JSON.stringify({ detail: "Slack is temporarily unavailable." }),
              { status: 502 }
            )
          : new Response("[]")
      )
    )
    renderSection()
    expect(
      await screen.findByText("Slack is temporarily unavailable.")
    ).toBeTruthy()
    fireEvent.click(
      screen.getByRole("button", { name: "Enter bot ID manually" })
    )
    fireEvent.change(screen.getByLabelText("Slack bot ID"), {
      target: { value: "B123" },
    })
    await chooseEnvironment()
    expect(screen.getByRole("button", { name: "Allow bot" })).toHaveProperty(
      "disabled",
      false
    )
  })

  it("does not show controls or load the allowlist for non-admins", async () => {
    const fetch = vi.fn()
    vi.stubGlobal("fetch", fetch)
    const view = renderSection(false)
    await waitFor(() => expect(view.container.innerHTML).toBe(""))
    expect(fetch).not.toHaveBeenCalled()
  })
})
