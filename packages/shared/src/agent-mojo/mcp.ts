import { z } from "zod";
import type { OAuthClientProvider } from "@modelcontextprotocol/sdk/client/auth.js";

export const oAuthClientProviderSchema = z.custom<OAuthClientProvider>(
  (val) => {
    if (!val || typeof val !== "object") return false;

    // Check required properties and methods exist
    const requiredMethods = [
      "redirectUrl",
      "clientMetadata",
      "clientInformation",
      "tokens",
      "saveTokens",
    ];

    // redirectUrl can be a string, URL, or getter returning string/URL
    if (!("redirectUrl" in val)) return false;

    // clientMetadata can be an object or getter returning an object
    if (!("clientMetadata" in val)) return false;

    // Check that required methods exist (they can be functions or getters)
    for (const method of requiredMethods) {
      if (!(method in val)) return false;
    }

    return true;
  },
  {
    message:
      "Must be a valid OAuthClientProvider implementation with required properties: redirectUrl, clientMetadata, clientInformation, tokens, saveTokens",
  },
);

/**
 * Stdio transport restart configuration
 */
export const stdioRestartSchema = z
  .object({
    /**
     * Whether to automatically restart the process if it exits
     */
    enabled: z
      .boolean()
      .describe("Whether to automatically restart the process if it exits")
      .optional(),
    /**
     * Maximum number of restart attempts
     */
    maxAttempts: z
      .number()
      .describe("The maximum number of restart attempts")
      .optional(),
    /**
     * Delay in milliseconds between restart attempts
     */
    delayMs: z
      .number()
      .describe("The delay in milliseconds between restart attempts")
      .optional(),
  })
  .describe("Configuration for stdio transport restart");

export const stdioConnectionSchema = z
  .object({
    /**
     * Optional transport type, inferred from the structure of the config if not provided. Included
     * for compatibility with common MCP client config file formats.
     */
    transport: z.literal("stdio").optional(),
    /**
     * Optional transport type, inferred from the structure of the config if not provided. Included
     * for compatibility with common MCP client config file formats.
     */
    type: z.literal("stdio").optional(),
    /**
     * The executable to run the server (e.g. `node`, `npx`, etc)
     */
    command: z.string().describe("The executable to run the server"),
    /**
     * Array of command line arguments to pass to the executable
     */
    args: z
      .array(z.string())
      .describe("Command line arguments to pass to the executable"),
    /**
     * Environment variables to set when spawning the process.
     */
    env: z
      .record(z.string())
      .describe("The environment to use when spawning the process")
      .optional(),
    /**
     * The encoding to use when reading from the process
     */
    encoding: z
      .string()
      .describe("The encoding to use when reading from the process")
      .optional(),
    /**
     * How to handle stderr of the child process. This matches the semantics of Node's `child_process.spawn`
     *
     * The default is "inherit", meaning messages to stderr will be printed to the parent process's stderr.
     *
     * @default "inherit"
     */
    stderr: z
      .union([
        z.literal("overlapped"),
        z.literal("pipe"),
        z.literal("ignore"),
        z.literal("inherit"),
      ])
      .optional()
      .default("inherit"),
    /**
     * The working directory to use when spawning the process.
     */
    cwd: z
      .string()
      .describe("The working directory to use when spawning the process")
      .optional(),
    /**
     * Additional restart settings
     */
    restart: stdioRestartSchema.optional(),
  })
  .describe("Configuration for stdio transport connection");

export const streamableHttpReconnectSchema = z
  .object({
    /**
     * Whether to automatically reconnect if the connection is lost
     */
    enabled: z
      .boolean()
      .describe("Whether to automatically reconnect if the connection is lost")
      .optional(),
    /**
     * Maximum number of reconnection attempts
     */
    maxAttempts: z
      .number()
      .describe("The maximum number of reconnection attempts")
      .optional(),
    /**
     * Delay in milliseconds between reconnection attempts
     */
    delayMs: z
      .number()
      .describe("The delay in milliseconds between reconnection attempts")
      .optional(),
  })
  .describe("Configuration for streamable HTTP transport reconnection");

/**
 * Streamable HTTP transport connection
 */
export const streamableHttpConnectionSchema = z
  .object({
    /**
     * Optional transport type, inferred from the structure of the config. If "sse", will not attempt
     * to connect using streamable HTTP.
     */
    transport: z.union([z.literal("http"), z.literal("sse")]).optional(),
    /**
     * Optional transport type, inferred from the structure of the config. If "sse", will not attempt
     * to connect using streamable HTTP.
     */
    type: z.union([z.literal("http"), z.literal("sse")]).optional(),
    /**
     * The URL to connect to
     */
    url: z.string().url(),
    /**
     * Additional headers to send with the request, useful for authentication
     */
    headers: z.record(z.string()).optional(),
    /**
     * OAuth client provider for automatic authentication handling.
     * When provided, the transport will automatically handle token refresh,
     * 401 error retries, and OAuth 2.0 flows according to RFC 6750.
     * This is the recommended approach for authentication instead of manual headers.
     */
    authProvider: oAuthClientProviderSchema.optional(),
    /**
     * Additional reconnection settings.
     */
    reconnect: streamableHttpReconnectSchema.optional(),
    /**
     * Whether to automatically fallback to SSE if Streamable HTTP is not available or not supported
     *
     * @default true
     */
    automaticSSEFallback: z.boolean().optional().default(true),
  })
  .describe("Configuration for streamable HTTP transport connection");

export const McpServerConfigSchema = z.union([
  stdioConnectionSchema,
  streamableHttpConnectionSchema,
]);

export type McpServerConfig = z.infer<typeof McpServerConfigSchema>;

export type McpServers = {
  /**
   * Map of server names to their configurations
   */
  [serverName: string]: McpServerConfig;
};
