import { GraphConfig } from "@openswe/shared/open-swe/types";
import {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import { resolveWorkspacePath } from "../../../utils/workspace.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "ResolveWorkspace");

export function resolveWorkspace(
  state: ManagerGraphState,
  _config: GraphConfig,
): ManagerGraphUpdate {
  if (state.workspacePath || !state.workspaceAbsPath) {
    return {};
  }

  try {
    const resolved = resolveWorkspacePath(state.workspaceAbsPath);
    logger.info("Resolved workspace path", { workspace: resolved });
    return { workspacePath: resolved };
  } catch (error) {
    logger.error("Failed to resolve workspace path", {
      error: error instanceof Error ? error.message : String(error),
    });
    throw error;
  }
}
