/** @vitest-environment jsdom */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"

import { McpConnectionsSection } from "./McpConnectionsSection"
import type { McpConnection, McpConnectionInput } from "@/lib/mcp"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const incident: McpConnection = {
  id: "c1",
  name: "incident",
  url: "https://mcp.incident.io/mcp",
  transport: "streamable_http",
  enabled: true,
  auth_type: "headers",
  tool_names: ["search"],
  tools: [{ name: "search", description: "Search incidents" }],
  allowed_tools: ["search"],
  status: "connected",
  headers_configured: true,
  header_names: ["Authorization"],
  bearer_token_configured: false,
  oauth_configured: false,
  oauth_client_secret_configured: false,
}

type Call = { method: string; url: string; body?: McpConnectionInput }

function mockApi(connections: Array<McpConnection>) {
  const calls: Array<Call> = []
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input)
    const method = init?.method ?? "GET"
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ method, url, body })
    const json = (value: unknown, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: { "Content-Type": "application/json" },
      })
    if (url.includes("/discover"))
      return json({
        tools: [
          { name: "search", description: "Search incidents" },
          { name: "delete", description: "Delete incident" },
        ],
      })
    if (url.includes("/headers/reveal"))
      return json({ Authorization: "Bearer test-secret" })
    if (method === "DELETE") {
      connections = connections.filter((c) => !url.includes(`/${c.id}?`))
      return new Response(null, { status: 204 })
    }
    if (method === "POST" || method === "PUT") {
      const saved: McpConnection = {
        ...incident,
        ...connections.find((c) => c.id === body?.id),
        ...body,
        id: body?.id ?? `c${connections.length + 1}`,
        header_names: body?.headers
          ? Object.keys(body.headers)
          : incident.header_names,
      }
      connections = [...connections.filter((c) => c.id !== saved.id), saved]
      return json(saved)
    }
    return json({ connections, presets: [] })
  })
  return calls
}

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <McpConnectionsSection scope="workspace" login="admin" />
    </QueryClientProvider>
  )
  return client
}

const writes = (calls: Array<Call>) =>
  calls.filter(
    (call) =>
      (call.method === "POST" || call.method === "PUT") &&
      !/\/(discover|test|reveal)\?/.test(call.url)
  )

async function ready(name: string) {
  const button = await screen.findByRole("button", { name })
  await waitFor(() =>
    expect((button as HTMLButtonElement).disabled).toBe(false)
  )
  return button
}

it("rejects an invalid workspace connection name before writing", async () => {
  const calls = mockApi([])
  mount()
  fireEvent.click(await ready("Add server"))
  fireEvent.change(screen.getByLabelText("Name"), {
    target: { value: "incident.io" },
  })
  fireEvent.change(screen.getByLabelText("Server URL"), {
    target: { value: "https://mcp.incident.io/mcp" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save server" }))
  expect((await screen.findByRole("alert")).textContent).toContain(
    "lowercase name"
  )
  expect(writes(calls)).toEqual([])
})

it("discovers tools with the draft headers before saving and preselects them", async () => {
  const calls = mockApi([])
  mount()
  fireEvent.click(await ready("Add server"))
  fireEvent.change(screen.getByLabelText("Name"), {
    target: { value: "incident" },
  })
  fireEvent.change(screen.getByLabelText("Server URL"), {
    target: { value: "https://mcp.incident.io/mcp" },
  })
  fireEvent.change(screen.getByLabelText("Transport"), {
    target: { value: "sse" },
  })
  fireEvent.change(screen.getByLabelText("Authentication"), {
    target: { value: "headers" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Add header" }))
  fireEvent.change(screen.getByLabelText("Header 1 name"), {
    target: { value: "Authorization" },
  })
  fireEvent.change(screen.getByLabelText("Header 1 value"), {
    target: { value: "Bearer test-secret" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Discover tools" }))
  const deleteTool = (await screen.findByRole("checkbox", {
    name: "Allow delete",
  })) as HTMLInputElement
  expect(deleteTool.checked).toBe(true)
  expect(writes(calls)).toEqual([])
  const discover = calls.find((call) => call.url.includes("/discover"))
  expect(discover?.url).toContain("scope=workspace")
  expect(discover?.body).toMatchObject({
    name: "incident",
    transport: "sse",
    headers: { Authorization: "Bearer test-secret" },
    allowed_tools: [],
  })
  fireEvent.click(deleteTool)
  fireEvent.click(screen.getByRole("button", { name: "Save server" }))
  await waitFor(() => expect(writes(calls)).toHaveLength(1))
  const [save] = writes(calls)
  expect(save?.method).toBe("POST")
  expect(save?.url).toContain("scope=workspace")
  expect(save?.body?.allowed_tools).toEqual(["search"])
  expect(save?.body?.headers).toEqual({ Authorization: "Bearer test-secret" })
  await screen.findByText("Connected · 1 tools")
})

it("keeps saved tool selections when rediscovering an existing connection", async () => {
  const calls = mockApi([{ ...incident, allowed_tools: [] }])
  mount()
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit incident cloud" })
  )
  fireEvent.click(screen.getByRole("button", { name: "Discover tools" }))
  const deleteTool = (await screen.findByRole("checkbox", {
    name: "Allow delete",
  })) as HTMLInputElement
  expect(deleteTool.checked).toBe(false)
  expect(
    (screen.getByRole("checkbox", { name: "Allow search" }) as HTMLInputElement)
      .checked
  ).toBe(false)
  fireEvent.click(screen.getByRole("button", { name: "Save server" }))
  await waitFor(() => expect(writes(calls)).toHaveLength(1))
  expect(writes(calls)[0]?.method).toBe("PUT")
  expect(writes(calls)[0]?.body?.allowed_tools).toEqual([])
  expect(writes(calls)[0]?.body).not.toHaveProperty("headers")
})

it("reveals saved headers on demand and keeps them unless edited", async () => {
  const calls = mockApi([incident])
  mount()
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit incident cloud" })
  )
  const value = screen.getByLabelText("Header 1 value") as HTMLInputElement
  expect(value.value).toBe("")
  fireEvent.click(screen.getByRole("button", { name: "Show saved headers" }))
  await waitFor(() => expect(value.value).toBe("Bearer test-secret"))
  expect(value.type).toBe("text")
  expect(
    calls.filter((call) => call.url.includes("/headers/reveal"))
  ).toHaveLength(1)
  fireEvent.click(screen.getByRole("button", { name: "Hide saved headers" }))
  expect(screen.queryByDisplayValue("Bearer test-secret")).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "Save server" }))
  await waitFor(() => expect(writes(calls)).toHaveLength(1))
  expect(writes(calls)[0]?.body).not.toHaveProperty("headers")
})

it("clears saved headers when every row is removed", async () => {
  const calls = mockApi([incident])
  mount()
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit incident cloud" })
  )
  fireEvent.click(screen.getByRole("button", { name: "Remove header 1" }))
  fireEvent.click(screen.getByRole("button", { name: "Save server" }))
  await waitFor(() => expect(writes(calls)).toHaveLength(1))
  expect(writes(calls)[0]?.body?.headers).toEqual({})
})

it("reviews imported connections one at a time without writing", async () => {
  const calls = mockApi([])
  mount()
  fireEvent.click(await ready("Import JSON"))
  fireEvent.change(screen.getByLabelText("MCP configuration JSON"), {
    target: {
      value: JSON.stringify({
        mcpServers: {
          incident: {
            url: "https://mcp.incident.io/mcp",
            headers: { Authorization: "Bearer test-secret" },
          },
          datadog: {
            type: "sse",
            url: "https://mcp.us5.datadoghq.com/v1/mcp",
            headers: { DD_API_KEY: "test-api", DD_APPLICATION_KEY: "test-app" },
          },
        },
      }),
    },
  })
  fireEvent.click(screen.getByRole("button", { name: "Review connections" }))
  expect(screen.queryByLabelText("MCP configuration JSON")).toBeNull()
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(
    "incident"
  )
  const secret = screen.getByLabelText("Header 1 value") as HTMLInputElement
  expect(secret.type).toBe("password")
  expect(secret.value).toBe("Bearer test-secret")
  fireEvent.click(screen.getByRole("button", { name: "Skip connection" }))
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(
    "datadog"
  )
  expect((screen.getByLabelText("Transport") as HTMLSelectElement).value).toBe(
    "sse"
  )
  expect(
    (screen.getByLabelText("Header 2 name") as HTMLInputElement).value
  ).toBe("DD_APPLICATION_KEY")
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
  expect(screen.queryByLabelText("Header 1 value")).toBeNull()
  expect(writes(calls)).toEqual([])
})
