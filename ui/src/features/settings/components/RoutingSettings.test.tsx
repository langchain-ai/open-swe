/** @vitest-environment jsdom */
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import type { RoutingSettings as RoutingData } from "@/lib/api"
import { RoutingSettings } from "./RoutingSettings"

afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.restoreAllMocks()
})

class RoutingSettingsServer {
  routing: RoutingData = {
    repos: [
      { repo: "acme/api", may_start_threads: false },
      { repo: "acme/web", may_start_threads: false },
    ],
    slack_channels: [
      { channel_id: "CTEAM0001", mode: "kitchen" },
      { channel_id: "COTHER001", mode: "ask" },
    ],
  }
  writes: Array<{ path: string; method: string; body: unknown }> = []
  pending: Promise<void> | undefined
  release: (() => void) | undefined
  failure: string | null = null

  holdNextSave() {
    this.pending = new Promise((resolve) => {
      this.release = resolve
    })
  }

  async request(input: RequestInfo | URL, init?: RequestInit) {
    const url = input instanceof Request ? input.url : String(input)
    const path = url.replace(/^.*\/dashboard\/api/, "")
    if (path === "/me") return Response.json({ login: "admin", is_admin: true })
    if (path === "/repos")
      return Response.json({
        installations: [],
        repositories: ["acme/api", "acme/web", "acme/new"].map((full_name) => ({
          full_name,
          private: true,
        })),
      })
    if (path === "/slack/channels")
      return Response.json({
        channels: [
          { id: "CTEAM0001", name: "team" },
          { id: "COTHER001", name: "other" },
        ].map((channel) => ({
          ...channel,
          is_member: true,
          is_private: false,
          is_ext_shared: false,
          num_members: 4,
        })),
        refreshing: false,
      })
    if (path === "/routing") return Response.json(this.routing)
    if (!path.startsWith("/routing/"))
      throw new Error(`Unexpected request: ${url}`)
    const method = init?.method ?? "GET"
    const body = init?.body ? JSON.parse(String(init.body)) : null
    this.writes.push({ path, method, body })
    const pending = this.pending
    this.pending = undefined
    await pending
    if (this.failure) {
      const detail = this.failure
      this.failure = null
      return Response.json({ detail }, { status: 422 })
    }
    if (path.startsWith("/routing/repositories/")) {
      const repo = path.slice("/routing/repositories/".length)
      this.routing.repos = this.routing.repos.filter((row) => row.repo !== repo)
      if (method === "PUT")
        this.routing.repos.push({
          repo,
          may_start_threads: body.may_start_threads,
        })
    } else {
      const channel_id = path.slice("/routing/slack-channels/".length)
      this.routing.slack_channels = this.routing.slack_channels.filter(
        (row) => row.channel_id !== channel_id
      )
      if (method === "PUT")
        this.routing.slack_channels.push({ channel_id, mode: body.mode })
    }
    return Response.json({})
  }

  render() {
    vi.spyOn(globalThis, "fetch").mockImplementation(this.request.bind(this))
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    })
    render(
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <RoutingSettings />
        </TooltipProvider>
      </QueryClientProvider>
    )
  }
}

it("optimistically changes Actions permission, locks concurrent writes, and rolls back failure", async () => {
  const server = new RoutingSettingsServer()
  server.failure = "Permission save refused"
  server.holdNextSave()
  server.render()
  const web = await screen.findByRole("switch", {
    name: "Let acme/web start threads",
  })
  fireEvent.click(web)
  await vi.waitFor(() =>
    expect(server.writes).toEqual([
      {
        path: "/routing/repositories/acme/web",
        method: "PUT",
        body: { may_start_threads: true },
      },
    ])
  )
  expect(web.getAttribute("aria-checked")).toBe("true")
  expect(web.hasAttribute("data-disabled")).toBe(true)
  const apiToggle = screen.getByRole("switch", {
    name: "Let acme/api start threads",
  })
  expect(apiToggle.hasAttribute("data-disabled")).toBe(true)
  fireEvent.click(apiToggle)
  expect(server.writes).toHaveLength(1)
  server.release?.()
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Permission save refused"
  )
  await vi.waitFor(() => expect(web.getAttribute("aria-checked")).toBe("false"))
})

it("adds a repository with Actions permission off without rewriting unrelated bindings", async () => {
  const server = new RoutingSettingsServer()
  server.render()
  const choose = await screen.findByRole("button", {
    name: "Choose repositories",
  })
  fireEvent.click(choose)
  fireEvent.click(await screen.findByRole("checkbox", { name: "new" }))
  fireEvent.click(screen.getByRole("button", { name: "Save 3 repositories" }))
  await vi.waitFor(() =>
    expect(server.writes).toEqual([
      {
        path: "/routing/repositories/acme/new",
        method: "PUT",
        body: { may_start_threads: false },
      },
    ])
  )
  expect(
    await screen.findByRole("switch", { name: "Let acme/new start threads" })
  ).toBeTruthy()
})

it("removes only the chosen channel binding", async () => {
  const server = new RoutingSettingsServer()
  server.render()
  fireEvent.click(
    await screen.findByRole("button", { name: "Choose channels" })
  )
  fireEvent.click(await screen.findByRole("checkbox", { name: "#team" }))
  fireEvent.click(screen.getByRole("button", { name: "Save 1 channel" }))
  await vi.waitFor(() =>
    expect(server.writes).toEqual([
      {
        path: "/routing/slack-channels/CTEAM0001",
        method: "DELETE",
        body: null,
      },
    ])
  )
  await vi.waitFor(() =>
    expect(
      screen.queryByRole("combobox", { name: "Channel type for #team" })
    ).toBeNull()
  )
  expect(
    screen.getByRole("combobox", { name: "Channel type for #other" })
      .textContent
  ).toContain("Ask")
})

it("rolls back a failed channel mode and retries without changing other bindings", async () => {
  const server = new RoutingSettingsServer()
  server.failure = "Channel mode refused"
  server.holdNextSave()
  server.render()
  const mode = await screen.findByRole("combobox", {
    name: "Channel type for #team",
  })
  fireEvent.click(mode)
  fireEvent.click(await screen.findByRole("option", { name: "Ask" }))
  await vi.waitFor(() => expect(server.writes).toHaveLength(1))
  expect(mode.textContent).toContain("Ask")
  server.release?.()
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Channel mode refused"
  )
  await vi.waitFor(() => expect(mode.textContent).toContain("Kitchen"))
  fireEvent.click(mode)
  fireEvent.click(await screen.findByRole("option", { name: "Ask" }))
  await vi.waitFor(() =>
    expect(server.routing.slack_channels).toEqual([
      { channel_id: "COTHER001", mode: "ask" },
      { channel_id: "CTEAM0001", mode: "ask" },
    ])
  )
  expect(server.writes[1]).toEqual({
    path: "/routing/slack-channels/CTEAM0001",
    method: "PUT",
    body: { mode: "ask" },
  })
})
