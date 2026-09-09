/** @vitest-environment jsdom */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"

import { WorkspaceMCPSection } from "./WorkspaceMCPSection"
import type { WorkspaceMCP, WorkspaceMCPUpdate } from "@/lib/api"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it("validates the connection name before saving and discovering tools", async () => {
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response(JSON.stringify([])))
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <WorkspaceMCPSection />
    </QueryClientProvider>
  )
  const add = screen.getByRole("button", { name: "Add MCP server" })
  await waitFor(() => expect((add as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(add)
  fireEvent.change(screen.getByLabelText("Connection name"), {
    target: { value: "incident.io" },
  })
  fireEvent.change(screen.getByLabelText("Server URL"), {
    target: { value: "https://mcp.incident.io/mcp" },
  })
  fireEvent.click(
    screen.getByRole("button", { name: "Save and discover tools" })
  )
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
    false
  )
  client.clear()
})

it("saves generic authentication, discovers tools, and enables only selected tools", async () => {
  let connection: WorkspaceMCP | null = null
  const writes: WorkspaceMCPUpdate[] = []
  const discoveries: WorkspaceMCPUpdate[] = []
  const operations: string[] = []
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input)
    let result: unknown
    if (url.endsWith("/discover")) {
      operations.push("discover")
      discoveries.push(JSON.parse(String(init?.body)))
      result = [
        { name: "search", description: "Search incidents" },
        { name: "delete", description: "Delete incident" },
      ]
    } else if (init?.method === "PUT") {
      operations.push("save")
      const update = JSON.parse(String(init.body)) as WorkspaceMCPUpdate
      writes.push(update)
      connection = {
        name: update.name,
        url: update.url,
        transport: update.transport,
        enabled: update.enabled,
        allowed_tools: update.allowed_tools,
        header_names: update.headers
          ? Object.keys(update.headers)
          : (connection?.header_names ?? []),
        revision: "v1",
        updated_at: "now",
      }
      result = connection
    } else if (init?.method === "DELETE") {
      connection = null
      return new Response(null, { status: 204 })
    } else {
      result = connection ? [connection] : []
    }
    return new Response(JSON.stringify(result), {
      headers: { "Content-Type": "application/json" },
    })
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <WorkspaceMCPSection />
    </QueryClientProvider>
  )
  const add = screen.getByRole("button", { name: "Add MCP server" })
  await waitFor(() => expect((add as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(add)
  fireEvent.change(screen.getByLabelText("Connection name"), {
    target: { value: "incident" },
  })
  fireEvent.change(screen.getByLabelText("Server URL"), {
    target: { value: "https://mcp.incident.io/mcp" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Add header" }))
  fireEvent.change(screen.getByLabelText("Header 1 name"), {
    target: { value: "Authorization" },
  })
  const secret = screen.getByLabelText("Header 1 value")
  expect(secret.getAttribute("type")).toBe("password")
  fireEvent.change(secret, { target: { value: "  Bearer test-secret  " } })
  fireEvent.click(screen.getByRole("button", { name: "Show header 1 value" }))
  expect(secret.getAttribute("type")).toBe("text")
  fireEvent.click(screen.getByRole("button", { name: "Hide header 1 value" }))
  expect(secret.getAttribute("type")).toBe("password")
  fireEvent.click(
    screen.getByRole("button", { name: "Save and discover tools" })
  )
  const search = await screen.findByRole("checkbox", {
    name: "Allow search",
  })
  expect((search as HTMLInputElement).checked).toBe(true)
  expect(operations).toEqual(["discover", "save"])
  expect(discoveries[0]?.headers).toEqual({
    Authorization: "  Bearer test-secret  ",
  })
  expect(writes[0]?.allowed_tools).toEqual([])
  expect(writes[0]?.headers).toEqual({
    Authorization: "  Bearer test-secret  ",
  })
  expect(screen.queryByLabelText("Header 1 value")).toBeNull()
  expect(
    (screen.getByRole("checkbox", { name: "Allow delete" }) as HTMLInputElement)
      .checked
  ).toBe(true)
  expect(screen.getByText("2 of 2 selected")).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Hide tools" }))
  expect(screen.queryByRole("checkbox", { name: "Allow search" })).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "Clear all" }))
  expect(screen.getByText("0 of 2 selected")).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Show tools" }))
  fireEvent.click(screen.getByRole("button", { name: "Select all" }))
  fireEvent.click(screen.getByRole("checkbox", { name: "Allow delete" }))
  expect(screen.getByText("1 of 2 selected")).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Save connection" }))
  await screen.findByRole("button", { name: "Add MCP server" })
  expect(writes[1]?.allowed_tools).toEqual(["search"])
  expect(writes[1]?.headers).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "Edit incident" }))
  const incidentCard = screen.getByRole("region", {
    name: "incident MCP connection",
  })
  expect(within(incidentCard).getByLabelText("Server URL")).toBeTruthy()
  expect(
    within(incidentCard)
      .getByRole("button", { name: "Close incident" })
      .getAttribute("aria-expanded")
  ).toBe("true")
  fireEvent.click(screen.getByRole("button", { name: "Clear all" }))
  expect(screen.getByText("0 of 1 selected")).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Select all" }))
  expect(
    (screen.getByRole("checkbox", { name: "Allow search" }) as HTMLInputElement)
      .checked
  ).toBe(true)
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
  expect(within(incidentCard).queryByLabelText("Server URL")).toBeNull()
  expect(
    within(incidentCard)
      .getByRole("button", { name: "Edit incident" })
      .getAttribute("aria-expanded")
  ).toBe("false")
  fireEvent.click(screen.getByRole("button", { name: "Disable incident" }))
  await screen.findByRole("button", { name: "Enable incident" })
  expect(writes[2]?.enabled).toBe(false)
  fireEvent.click(screen.getByRole("button", { name: "Delete incident" }))
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Edit incident" })).toBeNull()
  )
  client.clear()
})

it.each([{ allowedTools: [] }, { allowedTools: ["search"] }])(
  "preserves existing tool selections $allowedTools when rediscovering tools",
  async ({ allowedTools }) => {
    const connection: WorkspaceMCP = {
      name: "incident",
      url: "https://mcp.incident.io/mcp",
      transport: "streamable_http",
      enabled: true,
      allowed_tools: allowedTools,
      header_names: [],
      revision: "v1",
      updated_at: "now",
    }
    const writes: WorkspaceMCPUpdate[] = []
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      if (String(input).endsWith("/discover")) {
        return new Response(
          JSON.stringify([
            { name: "search", description: "Search incidents" },
            { name: "delete", description: "Delete incident" },
          ])
        )
      }
      if (init?.method === "PUT") {
        writes.push(JSON.parse(String(init.body)))
        return new Response(JSON.stringify(connection))
      }
      return new Response(JSON.stringify([connection]))
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={client}>
        <WorkspaceMCPSection />
      </QueryClientProvider>
    )
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit incident" })
    )
    fireEvent.click(
      screen.getByRole("button", { name: "Save and discover tools" })
    )
    const deleteTool = await screen.findByRole("checkbox", {
      name: "Allow delete",
    })
    expect((deleteTool as HTMLInputElement).checked).toBe(false)
    expect(
      (
        screen.getByRole("checkbox", {
          name: "Allow search",
        }) as HTMLInputElement
      ).checked
    ).toBe(allowedTools.includes("search"))
    fireEvent.click(screen.getByRole("button", { name: "Save connection" }))
    await screen.findByRole("button", { name: "Add MCP server" })
    expect(writes.map((update) => update.allowed_tools)).toEqual([
      allowedTools,
      allowedTools,
    ])
    client.clear()
  }
)

it("keeps a newly saved connection editable when refreshing the list fails", async () => {
  let connection: WorkspaceMCP | null = null
  const writes: WorkspaceMCPUpdate[] = []
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input).endsWith("/discover")) {
      return new Response(
        JSON.stringify([{ name: "search", description: "Search incidents" }])
      )
    }
    if (init?.method === "PUT") {
      const update = JSON.parse(String(init.body)) as WorkspaceMCPUpdate
      writes.push(update)
      connection = {
        name: update.name,
        url: update.url,
        transport: update.transport,
        enabled: update.enabled,
        allowed_tools: update.allowed_tools,
        header_names: [],
        revision: "v1",
        updated_at: "now",
      }
      return new Response(JSON.stringify(connection))
    }
    return connection
      ? new Response(JSON.stringify({ detail: "Settings unavailable" }), {
          status: 503,
        })
      : new Response(JSON.stringify([]))
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <WorkspaceMCPSection />
    </QueryClientProvider>
  )
  const add = screen.getByRole("button", { name: "Add MCP server" })
  await waitFor(() => expect((add as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(add)
  fireEvent.change(screen.getByLabelText("Connection name"), {
    target: { value: "incident" },
  })
  fireEvent.change(screen.getByLabelText("Server URL"), {
    target: { value: "https://mcp.incident.io/mcp" },
  })
  fireEvent.click(
    screen.getByRole("button", { name: "Save and discover tools" })
  )
  await screen.findByRole("alert")
  const search = await screen.findByRole("checkbox", { name: "Allow search" })
  expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy()
  expect((search as HTMLInputElement).checked).toBe(true)
  fireEvent.click(screen.getByRole("button", { name: "Save connection" }))
  await screen.findByRole("button", { name: "Add MCP server" })
  expect(writes.at(-1)?.allowed_tools).toEqual(["search"])
  expect(screen.getByText("· Enabled · 1 tools")).toBeTruthy()
  client.clear()
})

it("reveals saved headers on demand and discards them when hidden or closed", async () => {
  const connection: WorkspaceMCP = {
    name: "incident",
    url: "https://mcp.incident.io/mcp",
    transport: "streamable_http",
    enabled: true,
    allowed_tools: [],
    header_names: ["Authorization"],
    revision: "v1",
    updated_at: "now",
  }
  const writes: WorkspaceMCPUpdate[] = []
  let reveals = 0
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input).endsWith("/headers/reveal")) {
      reveals += 1
      return new Response(JSON.stringify({ Authorization: "test-secret" }))
    }
    if (init?.method === "PUT") {
      writes.push(JSON.parse(String(init.body)))
      return new Response(JSON.stringify(connection))
    }
    return new Response(JSON.stringify([connection]))
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <WorkspaceMCPSection />
    </QueryClientProvider>
  )
  fireEvent.click(await screen.findByRole("button", { name: "Edit incident" }))
  expect(reveals).toBe(0)
  fireEvent.click(screen.getByRole("button", { name: "Show saved headers" }))
  const value = await screen.findByLabelText("Saved Authorization value")
  expect((value as HTMLInputElement).value).toBe("test-secret")
  expect((value as HTMLInputElement).readOnly).toBe(true)
  fireEvent.click(screen.getByRole("button", { name: "Hide saved headers" }))
  expect(screen.queryByDisplayValue("test-secret")).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "Show saved headers" }))
  await screen.findByDisplayValue("test-secret")
  expect(reveals).toBe(2)
  fireEvent.click(screen.getByRole("button", { name: "Close incident" }))
  fireEvent.click(screen.getByRole("button", { name: "Edit incident" }))
  expect(screen.queryByDisplayValue("test-secret")).toBeNull()
  expect(reveals).toBe(2)
  fireEvent.click(screen.getByRole("button", { name: "Show saved headers" }))
  await screen.findByDisplayValue("test-secret")
  fireEvent.click(screen.getByRole("button", { name: "Save connection" }))
  await screen.findByRole("button", { name: "Add MCP server" })
  expect(writes[0]?.headers).toBeNull()
  expect(screen.queryByDisplayValue("test-secret")).toBeNull()
  client.clear()
})

it("keeps an unsaved draft and its headers when discovery fails", async () => {
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input) =>
      String(input).endsWith("/discover")
        ? new Response(
            JSON.stringify({ detail: "MCP tool discovery failed (HTTP 403)" }),
            { status: 400 }
          )
        : new Response(JSON.stringify([]))
    )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <WorkspaceMCPSection />
    </QueryClientProvider>
  )
  const add = screen.getByRole("button", { name: "Add MCP server" })
  await waitFor(() => expect((add as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(add)
  fireEvent.change(screen.getByLabelText("Connection name"), {
    target: { value: "datadog" },
  })
  fireEvent.change(screen.getByLabelText("Server URL"), {
    target: { value: "https://example.com/mcp" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Add header" }))
  fireEvent.change(screen.getByLabelText("Header 1 name"), {
    target: { value: "DD_API_KEY" },
  })
  fireEvent.change(screen.getByLabelText("Header 1 value"), {
    target: { value: "test-secret" },
  })
  fireEvent.click(
    screen.getByRole("button", { name: "Save and discover tools" })
  )
  await screen.findByRole("alert")
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
    false
  )
  expect(
    (screen.getByLabelText("Header 1 value") as HTMLInputElement).value
  ).toBe("test-secret")
  expect(
    screen.queryByRole("region", { name: "datadog MCP connection" })
  ).toBeNull()
  client.clear()
})

it("surfaces a settings error and prevents writing over an unknown list", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ detail: "Settings unavailable" }), {
      status: 503,
    })
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <WorkspaceMCPSection />
    </QueryClientProvider>
  )
  await screen.findByRole("alert")
  expect(
    (
      screen.getByRole("button", {
        name: "Add MCP server",
      }) as HTMLButtonElement
    ).disabled
  ).toBe(true)
  client.clear()
})

it("reviews imported connections one at a time without writing on import or skip", async () => {
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(JSON.stringify([])))
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <WorkspaceMCPSection />
    </QueryClientProvider>
  )
  const importButton = screen.getByRole("button", { name: "Import JSON" })
  await waitFor(() =>
    expect((importButton as HTMLButtonElement).disabled).toBe(false)
  )
  fireEvent.click(importButton)
  fireEvent.change(screen.getByLabelText("MCP configuration JSON"), {
    target: {
      value: JSON.stringify({
        mcpServers: {
          incident: {
            url: "https://mcp.incident.io/mcp",
            headers: { Authorization: "Bearer test-secret" },
          },
          datadog: {
            type: "http",
            url: "https://mcp.us5.datadoghq.com/v1/mcp",
            headers: { DD_API_KEY: "test-api", DD_APPLICATION_KEY: "test-app" },
          },
        },
      }),
    },
  })
  fireEvent.click(screen.getByRole("button", { name: "Review connections" }))
  expect(screen.queryByLabelText("MCP configuration JSON")).toBeNull()
  expect(
    (screen.getByLabelText("Connection name") as HTMLInputElement).value
  ).toBe("incident")
  expect(
    (screen.getByLabelText("Header 1 value") as HTMLInputElement).type
  ).toBe("password")
  expect(
    (screen.getByLabelText("Header 1 value") as HTMLInputElement).value
  ).toBe("Bearer test-secret")
  fireEvent.click(screen.getByRole("button", { name: "Skip connection" }))
  expect(
    (screen.getByLabelText("Connection name") as HTMLInputElement).value
  ).toBe("datadog")
  expect(
    (screen.getByLabelText("Header 2 name") as HTMLInputElement).value
  ).toBe("DD_APPLICATION_KEY")
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
  expect(screen.queryByLabelText("Header 1 value")).toBeNull()
  expect(
    fetchMock.mock.calls.some(
      ([, init]) => init?.method === "PUT" || init?.method === "POST"
    )
  ).toBe(false)
  client.clear()
})
