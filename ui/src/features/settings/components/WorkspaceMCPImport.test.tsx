/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { parseMCPConfig, WorkspaceMCPImport } from "./WorkspaceMCPImport"

afterEach(cleanup)

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
