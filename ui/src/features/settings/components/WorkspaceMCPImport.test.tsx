/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { parseMCPConfig, WorkspaceMCPImport } from "./WorkspaceMCPImport"

afterEach(cleanup)

it("imports OAuth client credentials without changing opaque secrets or scopes", () => {
  const oauth = {
    grant_type: "client_credentials",
    token_url: "https://api.linear.app/oauth/token",
    client_id: "test-app",
    client_secret: " test-secret ",
    scope: "read,write",
  }
  expect(
    parseMCPConfig(
      JSON.stringify({
        mcpServers: {
          linear: { url: "https://mcp.linear.app/mcp", oauth },
        },
      })
    )[0]?.oauth
  ).toEqual(oauth)
})

it.each([
  {
    client_id: "app",
    token_url: "http://example.com/token",
    client_secret: "test-secret",
  },
  {
    client_id: "app",
    token_url: "https://example.com/token",
    client_secret: { value: "test-secret" },
  },
  {
    client_id: "app",
    token_url: "https://example.com/token",
    client_secret: "${test-secret}",
  },
  {
    client_id: "app",
    token_url: "https://example.com/token",
    grant_type: "authorization_code",
  },
])("rejects unsupported OAuth settings without including secrets", (oauth) => {
  expect(() =>
    parseMCPConfig(
      JSON.stringify({
        mcpServers: {
          linear: { url: "https://mcp.linear.app/mcp", oauth },
        },
      })
    )
  ).toThrow(/OAuth/)
})

it("imports multiple remote Claude-style servers with headers and transports", () => {
  expect(
    parseMCPConfig(
      JSON.stringify({
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
          legacy: { type: "sse", url: "https://example.com/sse" },
        },
      })
    )
  ).toEqual([
    {
      name: "incident",
      transport: "streamable_http",
      url: "https://mcp.incident.io/mcp",
      headers: { Authorization: "Bearer test-secret" },
    },
    {
      name: "datadog",
      transport: "streamable_http",
      url: "https://mcp.us5.datadoghq.com/v1/mcp",
      headers: { DD_API_KEY: "test-api", DD_APPLICATION_KEY: "test-app" },
    },
    {
      name: "legacy",
      transport: "sse",
      url: "https://example.com/sse",
      headers: undefined,
    },
  ])
})

it.each([
  ['{"mcpServers": {"example": "test-secret"', "valid JSON"],
  [
    JSON.stringify({
      mcpServers: { example: { command: "test-secret", args: [] } },
    }),
    "local command",
  ],
  [
    JSON.stringify({
      mcpServers: {
        example: {
          url: "https://example.com",
          headers: { Authorization: "${test-secret}" },
        },
      },
    }),
    "environment-variable",
  ],
  [
    JSON.stringify({
      mcpServers: {
        example: {
          url: "https://example.com",
          headers: { Authorization: { secret: "test-secret" } },
        },
      },
    }),
    "string values",
  ],
  [
    JSON.stringify({
      mcpServers: { example: { url: "https://example.com", type: ["http"] } },
    }),
    "type must",
  ],
])(
  "rejects unsupported or malformed configuration without echoing secrets",
  (input, message) => {
    const onImport = vi.fn()
    render(<WorkspaceMCPImport onImport={onImport} onCancel={vi.fn()} />)
    fireEvent.change(screen.getByLabelText("MCP configuration JSON"), {
      target: { value: input },
    })
    fireEvent.click(screen.getByRole("button", { name: "Review connections" }))
    expect(screen.getByRole("alert").textContent).toContain(message)
    expect(screen.getByRole("alert").textContent).not.toContain("test-secret")
    expect(onImport).not.toHaveBeenCalled()
  }
)
