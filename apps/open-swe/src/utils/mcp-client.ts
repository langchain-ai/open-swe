import { MultiServerMCPClient } from "@langchain/mcp-adapters";
import type { StructuredToolInterface } from "@langchain/core/tools";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import {
  McpServerConfig,
  McpServerConfigSchema,
  McpServers,
} from "@open-swe/shared/open-swe/mcp";
import { createLogger, LogLevel } from "./logger.js";
import { DEFAULT_MCP_SERVERS } from "@open-swe/shared/constants";

const logger = createLogger(LogLevel.INFO, "MCP Client");

// Singleton instance of the MCP client
let mcpClientInstance: MultiServerMCPClient | null = null;
let lastConfigHash: string | null = null;

function isLangGraphDocsServer(server: McpServerConfig): boolean {
  if (!("command" in server) || !("args" in server)) return false;

  const langgraphMcpServer = Object.values(DEFAULT_MCP_SERVERS)[0];

  return (
    server.command === langgraphMcpServer.command &&
    server.args.every((arg, index) => arg === langgraphMcpServer.args[index])
  );
}

function validateMcpServers(mcpServers: McpServers): McpServers {
  try {
    const validatedServers: McpServers = {};

    for (const [serverName, config] of Object.entries(mcpServers)) {
      // Check if the server has http or sse transport/type
      const transport = config.transport || config.type;

      if (transport === "http" || transport === "sse") {
        validatedServers[serverName] = config;
      } else if (
        serverName === Object.keys(DEFAULT_MCP_SERVERS)[0] &&
        isLangGraphDocsServer(config)
      ) {
        // Allow LangGraphDocs server to be specified as a stdio server
        validatedServers[serverName] = config;
      } else {
        logger.info(
          `Skipping MCP server "${serverName}" - only http and sse transports are supported, got: ${transport || "undefined"}`,
        );
      }
    }

    return validatedServers;
  } catch (error) {
    logger.error("Failed to validate MCP servers: ", error);
    return {};
  }
}

/**
 * Returns a shared MCP client instance
 */
function mcpClient(mcpServers: McpServers): MultiServerMCPClient {
  const serversToUse = validateMcpServers(mcpServers);
  const configHash = JSON.stringify(serversToUse);

  // Recreate client if configuration changed
  if (!mcpClientInstance || lastConfigHash !== configHash) {
    mcpClientInstance = new MultiServerMCPClient({
      additionalToolNamePrefix: "",
      mcpServers: serversToUse,
    });
    lastConfigHash = configHash;
    logger.info(
      `MCP client initialized with ${Object.keys(serversToUse).length} servers: ${Object.keys(serversToUse).join(", ")}`,
    );
  }
  return mcpClientInstance;
}

/**
 * Gets MCP tools with configurable servers
 * @param config GraphConfig containing optional MCP servers configuration
 * @returns Array of MCP tools, empty array if error occurs
 */
export async function getMcpTools(
  config: GraphConfig,
): Promise<StructuredToolInterface[]> {
  try {
    // TODO: Remove default MCP servers obj once UI is implemented
    const mergedServers: McpServers = { ...DEFAULT_MCP_SERVERS };

    const mcpServersConfig = config?.configurable?.["mcpServers"];
    if (mcpServersConfig) {
      try {
        const userServers: McpServers = JSON.parse(mcpServersConfig);
        for (const serverName in userServers) {
          const serverConfig = userServers[serverName];
          if (!serverConfig) continue;
          try {
            McpServerConfigSchema.parse(serverConfig);
            mergedServers[serverName] = serverConfig;
          } catch (error) {
            logger.warn(
              `Failed to parse MCP server configuration for ${serverName}: ${error}. Skipping.`,
            );
          }
        }
      } catch (error) {
        logger.warn(
          `Failed to parse user MCP servers configuration: ${error}. Using defaults only.`,
        );
      }
    }

    if (!mergedServers) return [];

    const client = mcpClient(mergedServers);
    const tools = await client.getTools();
    return tools;
  } catch (error) {
    logger.error(`Error getting MCP tools: ${error}`);
    return [];
  }
}
