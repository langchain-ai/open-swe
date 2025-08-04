import { GraphConfig } from "@open-swe/shared/open-swe/types";

/**
 * Checks if the current execution context is in local mode
 * (working on local files instead of sandbox/Daytona)
 */
export function isLocalMode(config?: GraphConfig): boolean {
  if (!config) {
    return isLocalModeFromEnv();
  }
  return (config.configurable as any)?.["x-local-mode"] === "true";
}

/**
 * Gets the local working directory for local mode operations
 * Defaults to a test folder on the desktop if not specified
 */
export function getLocalWorkingDirectory(): string {
  return (
    process.env.OPEN_SWE_LOCAL_PROJECT_PATH ||
    process.env.OPEN_SWE_PROJECT_PATH ||
    process.cwd()
  );
}

/**
 * Checks if we're in local mode based on environment variables
 * (useful for contexts where GraphConfig is not available)
 */
export function isLocalModeFromEnv(): boolean {
  return process.env.OPEN_SWE_LOCAL_MODE === "true";
}
