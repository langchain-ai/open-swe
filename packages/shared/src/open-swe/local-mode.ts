import { GraphConfig } from "@open-swe/shared/open-swe/types";
import path from "path";

/**
 * Checks if the current execution context is in local mode
 * (working on local files instead of sandbox/Daytona)
 */
export function isLocalMode(config: GraphConfig): boolean {
  return (config.configurable as any)?.["x-local-mode"] === "true";
}

/**
 * Gets the local working directory for local mode operations
 * Defaults to a test folder on the desktop if not specified
 */
export function getLocalWorkingDirectory(): string {
  return (
    process.env.OPEN_SWE_PROJECT_PATH ||
    path.join(process.env.HOME || "", "Desktop", "test")
  );
}

/**
 * Checks if we're in local mode based on environment variables
 * (useful for contexts where GraphConfig is not available)
 */
export function isLocalModeFromEnv(): boolean {
  return process.env.OPEN_SWE_LOCAL_MODE === "true";
}
