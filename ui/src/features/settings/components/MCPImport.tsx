import { useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"

import type { MCPConnectionUpdate, MCPOAuthUpdate } from "@/lib/api"

export type ImportedMCP = Pick<
  MCPConnectionUpdate,
  "name" | "url" | "transport" | "headers" | "oauth" | "auth"
> & { allowed_tools?: string[] }

type ImportPreview = { connections: ImportedMCP[]; warnings: string[] }

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

export function parseMCPConfig(
  text: string,
  warnings: string[] = []
): ImportedMCP[] {
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

  const connections: ImportedMCP[] = []
  entries.forEach(([name, server], index) => {
    const label = /^[a-z][a-z0-9_-]{0,31}$/.test(name)
      ? `Connection ${index + 1} (${name})`
      : `Connection ${index + 1}`
    try {
      if (!/^[a-z][a-z0-9_-]{0,31}$/.test(name))
        throw new Error(
          `${label}: use a lowercase name with letters, numbers, hyphens, or underscores (1-32 characters).`
        )
      if (!isObject(server))
        throw new Error(`${label}: server settings must be an object.`)
      if ("command" in server || server.type === "stdio")
        throw new Error(
          `${label}: local command servers (including 1Password commands and macOS paths) run only on your machine and cannot be imported into hosted Open SWE. Use a remote HTTP or SSE server.`
        )
      if (
        Object.keys(server).some(
          (key) =>
            ![
              "url",
              "type",
              "headers",
              "oauth",
              "auth",
              "allowedTools",
            ].includes(key)
        )
      )
        throw new Error(
          `${label}: supported settings are url, type, headers, oauth, auth, and allowedTools.`
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
          Object.values(server.headers).some(
            (value) => typeof value !== "string"
          )
        )
          throw new Error(
            `${label}: headers must be an object of string values.`
          )
        headers = server.headers as Record<string, string>
        if (
          Object.entries(headers).some(
            ([key, value]) =>
              !value.trim() ||
              (key.toLowerCase() === "authorization" &&
                /^Bearer\s*$/i.test(value))
          )
        )
          throw new Error(
            `${label}: authentication headers need a value. Replace the empty bearer token before importing this connection.`
          )
        if (Object.values(headers).some((value) => /\$\{[^}]+\}/.test(value)))
          throw new Error(
            `${label}: replace environment-variable placeholders in headers with their values; this importer does not read local environment variables.`
          )
      }
      if (server.auth !== undefined && server.auth !== "oauth")
        throw new Error(`${label}: auth must be oauth for browser sign-in.`)
      if (server.auth === "oauth" && server.oauth != null)
        throw new Error(
          `${label}: choose browser OAuth or OAuth client credentials, not both.`
        )
      if (
        server.allowedTools !== undefined &&
        (!Array.isArray(server.allowedTools) ||
          server.allowedTools.some(
            (tool) =>
              typeof tool !== "string" || !tool.trim() || tool.includes("*")
          ))
      )
        throw new Error(
          `${label}: allowedTools must list explicit tool names; wildcards are not supported.`
        )
      connections.push({
        name,
        url: server.url.trim(),
        transport: server.type === "sse" ? "sse" : "streamable_http",
        headers,
        ...(server.allowedTools !== undefined
          ? { allowed_tools: [...new Set(server.allowedTools as string[])] }
          : {}),
        ...(server.auth === "oauth" ? { auth: "oauth" as const } : {}),
        ...(server.oauth !== undefined
          ? { oauth: parseOAuth(server.oauth, label) }
          : {}),
      })
    } catch (cause) {
      warnings.push(
        cause instanceof Error
          ? cause.message
          : `${label}: unsupported connection.`
      )
    }
  })
  if (!connections.length) throw new Error(warnings.join(" "))
  return connections
}

export function MCPImport({
  onImport,
  onCancel,
  allowOAuth = true,
}: {
  onImport: (connections: ImportedMCP[]) => void
  onCancel: () => void
  allowOAuth?: boolean
}) {
  const [text, setText] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  return (
    <form
      className="space-y-space-3 rounded-md border border-default p-space-4"
      onSubmit={(event) => {
        event.preventDefault()
        try {
          if (preview) {
            onImport(preview.connections)
            return
          }
          const warnings: string[] = []
          const connections = parseMCPConfig(text, warnings).filter(
            (connection) => {
              if (allowOAuth || connection.auth !== "oauth") return true
              warnings.push(
                `${connection.name}: browser OAuth is available only for personal connections.`
              )
              return false
            }
          )
          if (!connections.length) throw new Error(warnings.join(" "))
          setError(null)
          if (warnings.length) setPreview({ connections, warnings })
          else onImport(connections)
        } catch (cause) {
          setError(
            cause instanceof Error ? cause.message : "Unable to import JSON."
          )
        }
      }}
    >
      <p className="text-sm font-medium text-primary">Import MCP JSON</p>
      <p className="text-xs text-secondary">
        Paste a Claude-style mcpServers configuration. Review each connection
        before saving. Only explicitly selected tools are enabled. Local
        commands, including 1Password CLI and macOS paths, stay on your machine.
      </p>
      <Textarea
        aria-label="MCP configuration JSON"
        data-dd-privacy="hidden"
        size="md"
        inputClassName="h-48 max-h-64 font-mono"
        value={text}
        onChange={(value) => {
          setText(value)
          setPreview(null)
          setError(null)
        }}
        autoComplete="off"
        spellCheck={false}
        placeholder={
          '{\n  "mcpServers": {\n    "datadog": {\n      "type": "http",\n      "url": "https://mcp.us5.datadoghq.com/v1/mcp",\n      "headers": {\n        "DD_API_KEY": "YOUR_API_KEY",\n        "DD_APPLICATION_KEY": "YOUR_APPLICATION_KEY"\n      }\n    }\n  }\n}'
        }
      />
      {preview && (
        <div
          role="status"
          className="space-y-space-2 rounded-md border border-default bg-surface-level-2 p-space-3 text-sm text-secondary"
        >
          <p>
            {preview.connections.length} remote connections ready. Unsupported
            entries will be skipped:
          </p>
          <ul className="list-disc pl-space-4">
            {preview.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-error-secondary">
          {error}
        </p>
      )}
      <div className="flex gap-space-2">
        <Button type="submit" color="primary" size="xs" disabled={!text.trim()}>
          {preview ? "Review supported connections" : "Review connections"}
        </Button>
        <Button
          type="button"
          size="xs"
          color="secondary"
          variant="plain"
          onClick={onCancel}
        >
          {text ? "Cancel import" : "Close import"}
        </Button>
      </div>
    </form>
  )
}
