import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import type { MCPConnectionUpdate, MCPOAuthUpdate } from "@/lib/api"

export type ImportedMCP = Pick<
  MCPConnectionUpdate,
  "name" | "url" | "transport" | "headers" | "oauth"
>

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function parseOAuth(value: unknown, label: string): MCPOAuthUpdate | null {
  if (value === null) return null
  if (
    !isObject(value) ||
    Object.keys(value).some(
      (key) =>
        ![
          "grant_type",
          "token_url",
          "client_id",
          "client_secret",
          "scope",
          "token_endpoint_auth_method",
        ].includes(key)
    ) ||
    Object.values(value).some((item) => typeof item !== "string") ||
    typeof value.token_url !== "string" ||
    !value.token_url.startsWith("https://") ||
    typeof value.client_id !== "string" ||
    !value.client_id.trim() ||
    (value.grant_type !== undefined &&
      value.grant_type !== "client_credentials") ||
    (value.token_endpoint_auth_method !== undefined &&
      !["client_secret_post", "client_secret_basic"].includes(
        String(value.token_endpoint_auth_method)
      )) ||
    Object.values(value).some(
      (item) => typeof item === "string" && /\$\{[^}]+\}/.test(item)
    )
  )
    throw new Error(
      `${label}: OAuth requires an HTTPS token_url and client_id, supports only client_credentials, and does not resolve environment-variable placeholders.`
    )
  return { ...value, token_url: value.token_url, client_id: value.client_id }
}

export function parseMCPConfig(text: string): ImportedMCP[] {
  let config: unknown
  try {
    config = JSON.parse(text)
  } catch {
    throw new Error("Enter valid JSON with an mcpServers object.")
  }
  if (!isObject(config) || !isObject(config.mcpServers))
    throw new Error("The JSON must contain an mcpServers object.")
  const entries = Object.entries(config.mcpServers)
  if (!entries.length) throw new Error("Add at least one server to mcpServers.")

  return entries.map(([name, server], index) => {
    const label = `Connection ${index + 1}`
    if (!/^[a-z][a-z0-9_-]{0,31}$/.test(name))
      throw new Error(
        `${label}: use a lowercase name with letters, numbers, hyphens, or underscores (1-32 characters).`
      )
    if (!isObject(server))
      throw new Error(`${label}: server settings must be an object.`)
    if ("command" in server || server.type === "stdio")
      throw new Error(
        `${label}: local command servers are not supported. Use a remote HTTP or SSE server.`
      )
    if (
      Object.keys(server).some(
        (key) => !["url", "type", "headers", "oauth"].includes(key)
      )
    )
      throw new Error(
        `${label}: supported settings are url, type, headers, and oauth.`
      )
    if (
      typeof server.url !== "string" ||
      !server.url.trim().startsWith("https://")
    )
      throw new Error(`${label}: provide an HTTPS server URL.`)
    if (
      server.type !== undefined &&
      (typeof server.type !== "string" ||
        !["http", "streamable_http", "streamable-http", "sse"].includes(
          server.type
        ))
    )
      throw new Error(`${label}: type must be http or sse.`)
    let headers: Record<string, string> | undefined
    if (server.headers !== undefined) {
      if (
        !isObject(server.headers) ||
        Object.values(server.headers).some((value) => typeof value !== "string")
      )
        throw new Error(`${label}: headers must be an object of string values.`)
      headers = server.headers as Record<string, string>
      if (Object.values(headers).some((value) => /\$\{[^}]+\}/.test(value)))
        throw new Error(
          `${label}: replace environment-variable placeholders in headers with their values; this importer does not read local environment variables.`
        )
    }
    return {
      name,
      url: server.url.trim(),
      transport: server.type === "sse" ? "sse" : "streamable_http",
      headers,
      ...(server.oauth !== undefined
        ? { oauth: parseOAuth(server.oauth, label) }
        : {}),
    }
  })
}

export function MCPImport({
  onImport,
  onCancel,
}: {
  onImport: (connections: ImportedMCP[]) => void
  onCancel: () => void
}) {
  const [text, setText] = useState("")
  const [error, setError] = useState<string | null>(null)
  return (
    <form
      className="space-y-3 rounded-md border p-4"
      onSubmit={(event) => {
        event.preventDefault()
        try {
          const connections = parseMCPConfig(text)
          setText("")
          onImport(connections)
        } catch (cause) {
          setError(
            cause instanceof Error ? cause.message : "Unable to import JSON."
          )
        }
      }}
    >
      <p className="text-sm font-medium">Import MCP JSON</p>
      <p className="text-xs text-muted-foreground">
        Paste a Claude-style mcpServers configuration. Review each connection
        before saving. New connections preselect all tools after discovery.
      </p>
      <Textarea
        aria-label="MCP configuration JSON"
        className="h-48 max-h-64 resize-y font-mono"
        value={text}
        onChange={(event) => setText(event.target.value)}
        autoComplete="off"
        spellCheck={false}
        placeholder={
          '{\n  "mcpServers": {\n    "datadog": {\n      "type": "http",\n      "url": "https://mcp.us5.datadoghq.com/v1/mcp",\n      "headers": {\n        "DD_API_KEY": "YOUR_API_KEY",\n        "DD_APPLICATION_KEY": "YOUR_APPLICATION_KEY"\n      }\n    }\n  }\n}'
        }
      />
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={!text.trim()}>
          Review connections
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel import
        </Button>
      </div>
    </form>
  )
}
