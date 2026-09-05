export type McpAuthType = "none" | "bearer" | "headers" | "oauth"

export interface McpPreset {
  name: string
  url: string
  auth_type: McpAuthType
}

export interface McpConnection extends McpPreset {
  id: string
  enabled: boolean
  tool_names: Array<string>
  status: string
  headers_configured: boolean
  bearer_token_configured: boolean
  oauth_configured: boolean
  oauth_client_configured: boolean
  oauth_client_secret_configured: boolean
  oauth_client_id?: string
  oauth_scope?: string
  oauth_authorization_server?: string
  oauth_token_endpoint_auth_method?: McpConnectionInput["oauth_token_endpoint_auth_method"]
  tested_at: string | null
  created_at: string
  updated_at: string
}

export interface McpConnectionInput {
  id?: string
  name?: string
  url?: string
  enabled?: boolean
  auth_type?: McpAuthType
  headers?: Record<string, string>
  bearer_token?: string
  oauth_client_id?: string
  oauth_client_secret?: string
  oauth_authorization_server?: string
  oauth_scope?: string
  oauth_token_endpoint_auth_method?:
    | "none"
    | "client_secret_basic"
    | "client_secret_post"
}

export interface McpConnectionsPayload {
  connections: Array<McpConnection>
  presets: Array<McpPreset>
}

export interface LocalMcpServer {
  name: string
  transport: "stdio" | "streamable_http"
  enabled: boolean
  url?: string
  headers?: Record<string, string>
  command?: string
  args?: Array<string>
  env?: Record<string, string>
  env_passthrough?: Array<string>
  env_vars?: Array<string>
  cwd?: string
  auth_type?: "none" | "headers" | "oauth"
  oauth_client_id?: string
  oauth_scope?: string
  oauth_redirect_uri?: string
  oauth_token_endpoint_auth_method?: McpConnectionInput["oauth_token_endpoint_auth_method"]
  oauth_client_secret?: string
  oauth_client_secret_configured?: boolean
}

export interface McpDesktopBridge {
  getMcpServers: () => Promise<Array<LocalMcpServer>>
  saveMcpServer: (server: LocalMcpServer) => Promise<unknown>
  deleteMcpServer: (name: string) => Promise<unknown>
}

export function mcpDesktopBridge(): McpDesktopBridge | null {
  if (typeof window === "undefined") return null
  const bridge = window.openSweDesktop
  if (
    !bridge?.getMcpServers ||
    !bridge.saveMcpServer ||
    !bridge.deleteMcpServer
  )
    return null
  return {
    getMcpServers: bridge.getMcpServers,
    saveMcpServer: bridge.saveMcpServer,
    deleteMcpServer: bridge.deleteMcpServer,
  }
}
