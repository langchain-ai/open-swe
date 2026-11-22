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
    slack_channel_rules: [],
    repos: [{ repo: "acme/api" }, { repo: "acme/web" }],
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
          { id: "CINC00001", name: "inc-api" },
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
      if (method === "PUT") this.routing.repos.push({ repo })
    } else if (path.startsWith("/routing/slack-channel-rules/")) {
      const prefix = decodeURIComponent(
        path.slice("/routing/slack-channel-rules/".length)
      )
      this.routing.slack_channel_rules =
        this.routing.slack_channel_rules.filter((row) => row.prefix !== prefix)
      if (method === "PUT") {
        const rule = { prefix, prompt: body.prompt, kickoff: body.kickoff }
        this.routing.slack_channel_rules.push(rule)
        return Response.json(rule)
      }
      return new Response(null, { status: 204 })
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

it("binds a repository optimistically without rewriting unrelated bindings", async () => {
  const server = new RoutingSettingsServer()
  server.holdNextSave()
  server.render()
  fireEvent.click(
    await screen.findByRole("button", { name: "Choose repositories" })
  )
  fireEvent.click(await screen.findByRole("checkbox", { name: "new" }))
  fireEvent.click(screen.getByRole("button", { name: "Save 3 repositories" }))
  await vi.waitFor(() =>
    expect(server.writes).toEqual([
      { path: "/routing/repositories/acme/new", method: "PUT", body: null },
    ])
  )
  expect(screen.getByText("acme/new")).toBeTruthy()
  server.release?.()
  await vi.waitFor(() => expect(server.routing.repos).toHaveLength(3))
})

it("rolls back a repository binding the server refuses", async () => {
  const server = new RoutingSettingsServer()
  server.failure = "Binding refused"
  server.render()
  fireEvent.click(
    await screen.findByRole("button", { name: "Choose repositories" })
  )
  fireEvent.click(await screen.findByRole("checkbox", { name: "new" }))
  fireEvent.click(screen.getByRole("button", { name: "Save 3 repositories" }))
  expect((await screen.findByRole("alert")).textContent).toBe("Binding refused")
  await vi.waitFor(() => expect(screen.queryByText("acme/new")).toBeNull())
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

it("starts listening by name pattern from the same dialog and rolls back failed removal", async () => {
  const server = new RoutingSettingsServer()
  server.render()
  fireEvent.click(
    await screen.findByRole("button", { name: "Start listening" })
  )
  fireEvent.click(screen.getByRole("radio", { name: "Channels named…" }))
  fireEvent.change(
    screen.getByRole("textbox", { name: "Channel Name Starts With" }),
    { target: { value: "#INC-" } }
  )
  expect(
    await screen.findByText("Covers 1 public channel now: #inc-api.")
  ).toBeTruthy()
  const save = screen.getByRole<HTMLButtonElement>("button", {
    name: "Save and start listening",
  })
  expect(save.disabled).toBe(false)
  fireEvent.click(
    screen.getByRole("switch", {
      name: "Start a run when Robin joins a new matching channel",
    })
  )
  expect(save.disabled).toBe(true)
  fireEvent.change(
    screen.getByRole("textbox", { name: "On-join Instructions" }),
    { target: { value: "Investigate and report findings." } }
  )
  server.holdNextSave()
  fireEvent.click(
    screen.getByRole("button", { name: "Save and start listening" })
  )
  expect(await screen.findByText("#inc-*")).toBeTruthy()
  expect(server.routing.slack_channel_rules).toEqual([])
  server.release?.()
  await vi.waitFor(() =>
    expect(
      screen.queryByRole("textbox", { name: "Channel Name Starts With" })
    ).toBeNull()
  )
  expect(server.writes).toEqual([
    {
      path: "/routing/slack-channel-rules/inc-",
      method: "PUT",
      body: {
        prompt: "",
        kickoff: "Investigate and report findings.",
      },
    },
  ])
  expect(
    screen.getByText("1 channel now · joins new ones automatically")
  ).toBeTruthy()

  const stop = async () => {
    fireEvent.pointerDown(
      screen.getByRole("button", { name: "More actions for #inc-*" }),
      { button: 0, ctrlKey: false }
    )
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "Stop listening" })
    )
    const confirmation = screen.getByRole("dialog", {
      name: "Stop listening in #inc-*?",
    })
    fireEvent.click(
      within(confirmation).getByRole("button", { name: "Stop listening" })
    )
  }
  server.failure = "Rule removal refused"
  server.holdNextSave()
  await stop()
  await vi.waitFor(() => expect(screen.queryByText("#inc-*")).toBeNull())
  server.release?.()
  expect(await screen.findByText("Rule removal refused")).toBeTruthy()
  expect(await screen.findByText("#inc-*")).toBeTruthy()
  await stop()
  await vi.waitFor(() => expect(server.routing.slack_channel_rules).toEqual([]))
  expect(screen.queryByText("#inc-*")).toBeNull()
  expect(server.routing.slack_channels).toHaveLength(2)
})

it("flags channels whose own instructions win, retains a failed pattern edit, and can turn off its on-join run", async () => {
  const server = new RoutingSettingsServer()
  server.routing.slack_channel_rules = [
    { prefix: "inc-", prompt: "Triage.", kickoff: "Investigate." },
  ]
  server.routing.slack_channels.push({
    channel_id: "CINC00001",
    prompt: "Own instructions for this incident.",
  })
  server.render()
  expect(await screen.findByText("On join")).toBeTruthy()
  expect(
    await screen.findByText("0 channels now · joins new ones automatically")
  ).toBeTruthy()
  expect(screen.getByText("Own instructions")).toBeTruthy()
  fireEvent.click(
    screen.getByRole("button", { name: "Edit instructions for #inc-*" })
  )
  const toggle = screen.getByRole("switch", {
    name: "Start a run when Robin joins a new matching channel",
  })
  fireEvent.click(toggle)
  server.failure = "Rule edit refused"
  fireEvent.click(screen.getByRole("button", { name: "Save instructions" }))
  expect(await screen.findByText("Rule edit refused")).toBeTruthy()
  expect(server.routing.slack_channel_rules[0]?.kickoff).toBe("Investigate.")
  fireEvent.click(screen.getByRole("button", { name: "Save instructions" }))
  await vi.waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Save instructions" })
    ).toBeNull()
  )
  expect(server.routing.slack_channel_rules[0]).toEqual({
    prefix: "inc-",
    prompt: "Triage.",
    kickoff: "",
  })
  expect(screen.queryByText("On join")).toBeNull()
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
