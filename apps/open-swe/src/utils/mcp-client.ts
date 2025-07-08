import { MultiServerMCPClient } from "@langchain/mcp-adapters";
import type { StructuredToolInterface } from "@langchain/core/tools";
import {
  GraphConfig,
  McpServerConfigSchema,
  McpServers,
} from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "./logger.js";
import { DEFAULT_MCP_SERVERS } from "@open-swe/shared/constants";

const logger = createLogger(LogLevel.INFO, "MCP Client");

// Singleton instance of the MCP client
let mcpClientInstance: MultiServerMCPClient | null = null;
let lastConfigHash: string | null = null;

/**
 * Returns a shared MCP client instance
 */
export function mcpClient(mcpServers: McpServers): MultiServerMCPClient {
  const serversToUse = mcpServers;
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
    let mergedServers: McpServers = { ...DEFAULT_MCP_SERVERS };

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
