/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import type { ExecutionDefaults } from "@/lib/api"
import { ExecutionDefaultsSettings } from "./ExecutionDefaultsSettings"

class ExecutionSettingsServer {
  defaults: ExecutionDefaults = {
    enabled: false,
    prompt: "",
    repos: [],
    setup_script: "",
    update_script: "",
    snapshot_id: null,
    snapshot_name: null,
    base_snapshot_id: null,
    vcpus: null,
    mem_bytes: null,
    fs_capacity_bytes: null,
    create_params: {},
  }
  patches: Partial<ExecutionDefaults>[] = []
  failure: string | null = null
  pending: Promise<void> | undefined
  release: (() => void) | undefined
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })

  async request(input: RequestInfo | URL, init?: RequestInit) {
    const url = input instanceof Request ? input.url : String(input)
    if (url.endsWith("/me"))
      return Response.json({
        login: "admin",
        is_admin: true,
        base_orgs: ["org"],
      })
    if (!url.endsWith("/settings/execution"))
      throw new Error(`Unexpected request: ${url}`)
    if (init?.method === "PATCH") {
      const changes: Partial<ExecutionDefaults> = JSON.parse(String(init.body))
      this.patches.push(changes)
      const pending = this.pending
      this.pending = undefined
      await pending
      if (this.failure) {
        const detail = this.failure
        this.failure = null
        return Response.json({ detail }, { status: 422 })
      }
      this.defaults = { ...this.defaults, ...changes }
    }
    return Response.json(this.defaults)
  }

  holdNextSave() {
    this.pending = new Promise<void>((resolve) => {
      this.release = resolve
    })
  }

  render() {
    vi.spyOn(globalThis, "fetch").mockImplementation(this.request.bind(this))
    return render(
      <QueryClientProvider client={this.client}>
        <TooltipProvider>
          <ExecutionDefaultsSettings
            repositories={[{ full_name: "org/repo" }]}
          />
        </TooltipProvider>
      </QueryClientProvider>
    )
  }
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it("keeps instruction and preload drafts after a failed save and retries only those fields", async () => {
  const server = new ExecutionSettingsServer()
  server.failure = "Instructions could not be saved."
  server.render()

  fireEvent.change(
    await screen.findByRole("textbox", { name: "Instructions" }),
    {
      target: { value: "Run focused checks." },
    }
  )
  fireEvent.click(
    screen.getByRole("button", { name: "Add repository to preload" })
  )
  fireEvent.click(await screen.findByRole("button", { name: "org/repo" }))
  fireEvent.click(
    screen.getByRole("button", { name: "Save instructions and repositories" })
  )

  expect((await screen.findByRole("alert")).textContent).toBe(
    "Instructions could not be saved."
  )
  expect(screen.getByDisplayValue("Run focused checks.")).toBeTruthy()
  expect(
    screen.getByRole("button", {
      name: "Remove org/repo from preloaded repositories",
    })
  ).toBeTruthy()
  expect(server.defaults.prompt).toBe("")

  fireEvent.click(
    screen.getByRole("button", { name: "Save instructions and repositories" })
  )
  await vi.waitFor(() =>
    expect(server.defaults.prompt).toBe("Run focused checks.")
  )
  expect(server.patches).toEqual([
    { prompt: "Run focused checks.", repos: ["org/repo"] },
    { prompt: "Run focused checks.", repos: ["org/repo"] },
  ])
  expect(screen.queryByRole("alert")).toBeNull()
})

it("serializes saves across sections and preserves unrelated drafts and unknown create parameters", async () => {
  const server = new ExecutionSettingsServer()
  server.defaults = {
    ...server.defaults,
    vcpus: 4,
    snapshot_id: "old-image",
    create_params: {
      custom_option: { keep: true },
      proxy_config: { future_option: "keep", rules: [] },
    },
  }
  server.holdNextSave()
  server.render()

  fireEvent.change(
    await screen.findByRole("textbox", { name: "Instructions" }),
    {
      target: { value: "Unsaved instructions" },
    }
  )
  fireEvent.change(screen.getByRole("textbox", { name: "Snapshot ID" }), {
    target: { value: "" },
  })
  fireEvent.change(screen.getByRole("spinbutton", { name: "Memory (GiB)" }), {
    target: { value: "1.5" },
  })
  fireEvent.click(screen.getByRole("tab", { name: "JSON" }))
  fireEvent.change(
    screen.getByRole("textbox", { name: "Proxy configuration (JSON)" }),
    {
      target: {
        value: JSON.stringify(
          {
            future_option: "keep",
            rules: [{ name: "example", match_hosts: ["example.com"] }],
          },
          null,
          2
        ),
      },
    }
  )
  fireEvent.click(
    screen.getByRole("button", { name: "Save sandbox configuration" })
  )
  await vi.waitFor(() => expect(server.patches).toHaveLength(1))
  fireEvent.change(
    screen.getByRole("textbox", {
      name: "Additional Create Parameters (JSON)",
    }),
    {
      target: {
        value: JSON.stringify({
          custom_option: { keep: true },
          region: "us-east-1",
          mount_config: { mounts: [] },
        }),
      },
    }
  )
  fireEvent.click(
    screen.getByRole("button", { name: "Save sandbox parameters" })
  )
  fireEvent.click(
    screen.getByRole("button", { name: "Save proxy configuration" })
  )
  await vi.waitFor(() =>
    expect(screen.getAllByRole("button", { name: "Saving…" })).toHaveLength(3)
  )
  expect(server.patches).toHaveLength(1)

  server.release?.()
  await vi.waitFor(() =>
    expect(server.defaults.mem_bytes).toBe(1.5 * 1024 ** 3)
  )
  await vi.waitFor(() => expect(server.patches).toHaveLength(3))
  await vi.waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Save proxy configuration" })
        .hasAttribute("disabled")
    ).toBe(true)
  )
  expect(server.patches[0]).toEqual({
    snapshot_id: null,
    snapshot_name: null,
    base_snapshot_id: null,
    vcpus: 4,
    mem_bytes: 1.5 * 1024 ** 3,
    fs_capacity_bytes: null,
  })
  expect(server.defaults.snapshot_id).toBeNull()
  expect(server.defaults.create_params).toEqual({
    custom_option: { keep: true },
    region: "us-east-1",
    mount_config: { mounts: [] },
    proxy_config: {
      future_option: "keep",
      rules: [{ name: "example", match_hosts: ["example.com"] }],
    },
  })
  expect(server.defaults.prompt).toBe("")
  expect(screen.getByDisplayValue("Unsaved instructions")).toBeTruthy()
})

it("validates additional parameters and retries provider and mount changes without losing managed settings", async () => {
  const server = new ExecutionSettingsServer()
  server.defaults.create_params = {
    region: "us-east-1",
    obsolete_option: true,
    proxy_config: { rules: [] },
    access_delegation: { mode: "custom", permissions: ["traces:read"] },
  }
  server.render()
  const parameters = await screen.findByRole("textbox", {
    name: "Additional Create Parameters (JSON)",
  })
  fireEvent.change(parameters, { target: { value: "[]" } })
  fireEvent.click(
    screen.getByRole("button", { name: "Save sandbox parameters" })
  )
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Additional create parameters must be a JSON object."
  )
  expect(server.patches).toEqual([])

  const draft = JSON.stringify({
    region: "eu-west-1",
    mount_config: { mounts: [] },
  })
  fireEvent.change(parameters, { target: { value: draft } })
  server.failure = "Unsupported mount configuration."
  fireEvent.click(
    screen.getByRole("button", { name: "Save sandbox parameters" })
  )
  await vi.waitFor(() =>
    expect(screen.getByRole("alert").textContent).toBe(
      "Unsupported mount configuration."
    )
  )
  expect(screen.getByDisplayValue(draft)).toBeTruthy()
  expect(server.defaults.create_params.region).toBe("us-east-1")

  fireEvent.click(
    screen.getByRole("button", { name: "Save sandbox parameters" })
  )
  await vi.waitFor(() =>
    expect(server.defaults.create_params).toEqual({
      region: "eu-west-1",
      mount_config: { mounts: [] },
      proxy_config: { rules: [] },
      access_delegation: { mode: "custom", permissions: ["traces:read"] },
    })
  )
  await vi.waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Save sandbox parameters" })
        .hasAttribute("disabled")
    ).toBe(true)
  )
  expect(screen.queryByRole("alert")).toBeNull()
})

it("rejects invalid sandbox sizes without saving and keeps valid configuration editable", async () => {
  const server = new ExecutionSettingsServer()
  server.render()
  fireEvent.change(await screen.findByRole("spinbutton", { name: "vCPUs" }), {
    target: { value: "0" },
  })
  fireEvent.click(
    screen.getByRole("button", { name: "Save sandbox configuration" })
  )
  expect((await screen.findByRole("alert")).textContent).toBe(
    "vCPUs must be positive and resolve to whole bytes or vCPUs."
  )
  expect(server.patches).toEqual([])

  fireEvent.change(screen.getByRole("spinbutton", { name: "vCPUs" }), {
    target: { value: "2" },
  })
  fireEvent.click(
    screen.getByRole("button", { name: "Save sandbox configuration" })
  )
  await vi.waitFor(() => expect(server.defaults.vcpus).toBe(2))
})

it("leaves the cutover off on load and waits for a confirmed field-only save before changing it", async () => {
  const server = new ExecutionSettingsServer()
  server.defaults.create_params = { custom_option: { keep: true } }
  server.failure = "Activation could not be saved."
  server.holdNextSave()
  server.render()
  const toggle = await screen.findByRole("switch", {
    name: "Use Robin-wide Defaults",
  })
  expect(toggle.getAttribute("aria-checked")).toBe("false")
  expect(server.patches).toEqual([])
  fireEvent.click(toggle)
  await vi.waitFor(() => expect(server.patches).toEqual([{ enabled: true }]))
  expect(toggle.getAttribute("aria-checked")).toBe("false")
  expect(toggle.hasAttribute("data-disabled")).toBe(true)
  server.release?.()
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Activation could not be saved."
  )
  expect(toggle.getAttribute("aria-checked")).toBe("false")
  fireEvent.click(toggle)
  await vi.waitFor(() => {
    expect(toggle.getAttribute("aria-checked")).toBe("true")
    expect(toggle.hasAttribute("data-disabled")).toBe(false)
  })
  fireEvent.click(toggle)
  await vi.waitFor(() =>
    expect(toggle.getAttribute("aria-checked")).toBe("false")
  )
  expect(server.patches).toEqual([
    { enabled: true },
    { enabled: true },
    { enabled: false },
  ])
  expect(server.defaults.create_params).toEqual({
    custom_option: { keep: true },
  })
})
