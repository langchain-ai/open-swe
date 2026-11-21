/** @vitest-environment jsdom */
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import type { RoutingSettings as RoutingData } from "@/lib/api"
import { RoutingSettings } from "./RoutingSettings"
import { CHANNEL_PROMPT_TEMPLATES } from "@/features/settings/lib/channel-prompt-templates"

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
      { channel_id: "CTEAM0001", prompt: "Help with engineering work." },
      {
        channel_id: "COTHER001",
        prompt: "Investigate reports before proposing fixes.",
      },
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
          { id: "CNEW00001", name: "new" },
          { id: "CABSENT01", name: "not-joined" },
          { id: "CSHARED01", name: "external" },
        ].map((channel) => ({
          ...channel,
          is_member: channel.id !== "CABSENT01",
          is_private: false,
          is_ext_shared: channel.id === "CSHARED01",
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
        this.routing.slack_channels.push({ channel_id, prompt: body.prompt })
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

it("copies a template without writing and preserves a customized listening draft after failure", async () => {
  const server = new RoutingSettingsServer()
  server.failure = "Channel save refused"
  server.holdNextSave()
  server.render()
  fireEvent.click(
    await screen.findByRole("button", { name: "Start listening" })
  )
  fireEvent.click(
    screen.getByRole("combobox", { name: "Channel to listen to" })
  )
  fireEvent.click(await screen.findByRole("option", { name: /new/ }))
  expect(
    screen.getByRole<HTMLButtonElement>("button", {
      name: "Save and start listening",
    }).disabled
  ).toBe(true)
  fireEvent.click(screen.getByRole("radio", { name: "Ask" }))
  const instructions = screen.getByRole<HTMLTextAreaElement>("textbox", {
    name: "Instructions for #new",
  })
  expect(instructions.value).toBe(
    CHANNEL_PROMPT_TEMPLATES.find((template) => template.id === "ask")?.prompt
  )
  expect(server.writes).toHaveLength(0)
  fireEvent.change(instructions, {
    target: { value: "Investigate first. The DRI is @owner." },
  })
  fireEvent.click(
    screen.getByRole("button", { name: "Save and start listening" })
  )
  await vi.waitFor(() =>
    expect(server.writes).toEqual([
      {
        path: "/routing/slack-channels/CNEW00001",
        method: "PUT",
        body: { prompt: "Investigate first. The DRI is @owner." },
      },
    ])
  )
  expect(instructions.disabled).toBe(true)
  expect(
    screen.getByRole<HTMLButtonElement>("button", {
      name: "Save and start listening",
    }).disabled
  ).toBe(true)
  server.release?.()
  expect(await screen.findByText("Channel save refused")).toBeTruthy()
  await vi.waitFor(() => expect(instructions.disabled).toBe(false))
  expect(instructions.value).toBe("Investigate first. The DRI is @owner.")
  expect(
    server.routing.slack_channels.some((row) => row.channel_id === "CNEW00001")
  ).toBe(false)
  fireEvent.click(
    screen.getByRole("button", { name: "Save and start listening" })
  )
  expect(
    await screen.findByRole("button", { name: "Edit instructions for #new" })
  ).toBeTruthy()
  expect(server.writes[1]).toEqual(server.writes[0])
})

it("blocks a retry after another admin starts listening in the same channel", async () => {
  const server = new RoutingSettingsServer()
  server.failure = "Channel save refused"
  server.holdNextSave()
  server.render()
  fireEvent.click(
    await screen.findByRole("button", { name: "Start listening" })
  )
  fireEvent.click(
    screen.getByRole("combobox", { name: "Channel to listen to" })
  )
  fireEvent.click(await screen.findByRole("option", { name: /new/ }))
  fireEvent.change(
    screen.getByRole("textbox", { name: "Instructions for #new" }),
    { target: { value: "Fix failures immediately." } }
  )
  fireEvent.click(
    screen.getByRole("button", { name: "Save and start listening" })
  )
  await vi.waitFor(() => expect(server.writes).toHaveLength(1))
  server.routing.slack_channels.push({
    channel_id: "CNEW00001",
    prompt: "Investigate build failures; ask before fixing.",
  })
  server.release?.()
  expect(await screen.findByText("Channel save refused")).toBeTruthy()
  expect(
    await screen.findByText(/Robin is already listening here/)
  ).toBeTruthy()
  const save = screen.getByRole<HTMLButtonElement>("button", {
    name: "Save and start listening",
  })
  expect(save.disabled).toBe(true)
  fireEvent.click(save)
  expect(server.writes).toHaveLength(1)
  expect(
    server.routing.slack_channels.find((row) => row.channel_id === "CNEW00001")
      ?.prompt
  ).toBe("Investigate build failures; ask before fixing.")
})

it("asks before replacing existing instructions and saves only after explicit confirmation and save", async () => {
  const server = new RoutingSettingsServer()
  server.render()
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit instructions for #team" })
  )
  const instructions = screen.getByRole<HTMLTextAreaElement>("textbox", {
    name: "Instructions for #team",
  })
  fireEvent.change(instructions, {
    target: { value: "My unsaved instructions" },
  })
  fireEvent.click(screen.getByRole("radio", { name: "Kitchen" }))
  let confirmation = screen.getByRole("dialog", {
    name: "Replace channel instructions?",
  })
  fireEvent.click(within(confirmation).getByRole("button", { name: "Cancel" }))
  expect(instructions.value).toBe("My unsaved instructions")
  fireEvent.click(screen.getByRole("radio", { name: "Kitchen" }))
  confirmation = screen.getByRole("dialog", {
    name: "Replace channel instructions?",
  })
  fireEvent.click(
    within(confirmation).getByRole("button", { name: "Replace instructions" })
  )
  const kitchen = CHANNEL_PROMPT_TEMPLATES.find(
    (template) => template.id === "kitchen"
  )?.prompt
  expect(instructions.value).toBe(kitchen)
  expect(server.writes).toHaveLength(0)
  fireEvent.click(screen.getByRole("button", { name: "Save instructions" }))
  await vi.waitFor(() =>
    expect(server.writes).toEqual([
      {
        path: "/routing/slack-channels/CTEAM0001",
        method: "PUT",
        body: { prompt: kitchen },
      },
    ])
  )
  expect(
    await screen.findByRole("button", { name: "Edit instructions for #team" })
  ).toBeTruthy()
})

it("summarizes each channel's instructions and keeps the full text one click away", async () => {
  const kitchen = CHANNEL_PROMPT_TEMPLATES.find(
    (template) => template.id === "kitchen"
  )
  if (!kitchen) throw new Error("Missing Kitchen template")
  const server = new RoutingSettingsServer()
  server.routing.slack_channels = [
    { channel_id: "CTEAM0001", prompt: kitchen.prompt },
    { channel_id: "COTHER001", prompt: "Answer questions. Ask first." },
  ]
  server.render()
  const summary = await screen.findByText(kitchen.description)
  expect(summary.closest("details")?.querySelector("pre")?.textContent).toBe(
    kitchen.prompt
  )
  expect(screen.getByText("Answer questions.")).toBeTruthy()
  expect(screen.getByText("Answer questions. Ask first.").tagName).toBe("PRE")
})

it("removes only the chosen channel binding", async () => {
  const server = new RoutingSettingsServer()
  server.render()
  fireEvent.pointerDown(
    await screen.findByRole("button", { name: "More actions for #team" }),
    { button: 0, ctrlKey: false }
  )
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Stop listening" })
  )
  const confirmation = screen.getByRole("dialog", {
    name: "Stop listening in #team?",
  })
  expect(server.writes).toHaveLength(0)
  fireEvent.click(
    within(confirmation).getByRole("button", { name: "Stop listening" })
  )
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
      screen.queryByRole("button", { name: "Edit instructions for #team" })
    ).toBeNull()
  )
  expect(
    screen.getByText("Investigate reports before proposing fixes.")
  ).toBeTruthy()
})

it("preserves failed instruction edits for retry without changing other channels", async () => {
  const server = new RoutingSettingsServer()
  server.failure = "Channel instructions refused"
  server.holdNextSave()
  server.render()
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit instructions for #team" })
  )
  const instructions = screen.getByRole<HTMLTextAreaElement>("textbox", {
    name: "Instructions for #team",
  })
  fireEvent.change(instructions, {
    target: { value: "Triage reports. The DRI is @owner." },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save instructions" }))
  await vi.waitFor(() => expect(server.writes).toHaveLength(1))
  expect(instructions.disabled).toBe(true)
  server.release?.()
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Channel instructions refused"
  )
  await vi.waitFor(() => expect(instructions.disabled).toBe(false))
  expect(instructions.value).toBe("Triage reports. The DRI is @owner.")
  expect(server.routing.slack_channels[0]?.prompt).toBe(
    "Help with engineering work."
  )
  fireEvent.click(screen.getByRole("button", { name: "Save instructions" }))
  expect(
    await screen.findByText("Triage reports. The DRI is @owner.")
  ).toBeTruthy()
  expect(server.routing.slack_channels).toEqual([
    {
      channel_id: "COTHER001",
      prompt: "Investigate reports before proposing fixes.",
    },
    { channel_id: "CTEAM0001", prompt: "Triage reports. The DRI is @owner." },
  ])
  expect(server.writes[1]).toEqual({
    path: "/routing/slack-channels/CTEAM0001",
    method: "PUT",
    body: { prompt: "Triage reports. The DRI is @owner." },
  })
  fireEvent.click(
    screen.getByRole("button", { name: "Edit instructions for #team" })
  )
  fireEvent.change(
    screen.getByRole("textbox", { name: "Instructions for #team" }),
    { target: { value: "Unsaved" } }
  )
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
  expect(screen.getByText("Triage reports. The DRI is @owner.")).toBeTruthy()
  expect(server.writes).toHaveLength(2)
})

it("offers only eligible unbound channels and never starts listening to an unverified pasted channel", async () => {
  const server = new RoutingSettingsServer()
  server.render()
  fireEvent.click(
    await screen.findByRole("button", { name: "Start listening" })
  )
  const channel = screen.getByRole("combobox", { name: "Channel to listen to" })
  fireEvent.click(channel)
  expect(await screen.findByRole("option", { name: /new/ })).toBeTruthy()
  expect(screen.queryByRole("option", { name: /not-joined/ })).toBeNull()
  expect(screen.queryByRole("option", { name: /external/ })).toBeNull()
  expect(screen.queryByRole("option", { name: /team/ })).toBeNull()
  fireEvent.change(channel, { target: { value: "CUNKNOWN1" } })
  fireEvent.click(await screen.findByRole("option", { name: /CUNKNOWN1/ }))
  expect(
    screen.getByRole<HTMLButtonElement>("button", {
      name: "Save and start listening",
    }).disabled
  ).toBe(true)
  expect(screen.getByRole("status").textContent).toContain(
    "Choose an internal channel Robin has joined"
  )
  fireEvent.click(screen.getByRole("radio", { name: "Custom" }))
  fireEvent.change(screen.getByRole("textbox", { name: /Instructions for/ }), {
    target: { value: "My channel instructions" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
  expect(server.writes).toHaveLength(0)
  expect(screen.queryByRole("dialog")).toBeNull()
})
