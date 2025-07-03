import { MultiServerMCPClient } from "@langchain/mcp-adapters";
import { createLogger, LogLevel } from "./logger.js";
import { MCP_SERVERS } from "../constants.js";

const logger = createLogger(LogLevel.INFO, "MCP Client");

// Singleton instance of the MCP client
let mcpClientInstance: MultiServerMCPClient | null = null;


/**
 * Returns a shared MCP client instance
 */
export function mcpClient(): MultiServerMCPClient {
    if (!mcpClientInstance) {
        mcpClientInstance = new MultiServerMCPClient({
            additionalToolNamePrefix: "",
            mcpServers: MCP_SERVERS
        })
        logger.info(`MCP client initialized with ${Object.keys(MCP_SERVERS).length} servers`);
    }
    return mcpClientInstance;
}