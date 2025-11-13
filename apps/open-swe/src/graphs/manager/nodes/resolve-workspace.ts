import path from "node:path";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import { FeatureGraph, loadFeatureGraph } from "@openswe/shared/feature-graph";
import {
  resolveWorkspacePath,
  resolvePathInsideWorkspace,
} from "../../../utils/workspace.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "ResolveWorkspace");

const FEATURE_GRAPH_RELATIVE_PATH = path.join(
  "features",
  "graph",
  "graph.yaml",
);

export async function resolveWorkspace(
  state: ManagerGraphState,
  _config: GraphConfig,
): Promise<ManagerGraphUpdate> {
  if (!state.workspaceAbsPath) {
    return {};
  }

  const updates: ManagerGraphUpdate = {};

  try {
    let workspacePath = state.workspacePath;
    if (!workspacePath) {
      workspacePath = resolveWorkspacePath(state.workspaceAbsPath);
      logger.info("Resolved workspace path", { workspace: workspacePath });
      updates.workspacePath = workspacePath;
    }

    if (!state.featureGraph && workspacePath) {
      const graphPath = resolvePathInsideWorkspace(
        workspacePath,
        FEATURE_GRAPH_RELATIVE_PATH,
      );
      logger.info("Loading feature graph", { graphPath });
      const data = await loadFeatureGraph(graphPath);
      updates.featureGraph = new FeatureGraph(data);
      logger.info("Loaded feature graph", {
        graphPath,
        featureCount: data.nodes.size,
        edgeCount: data.edges.length,
        version: data.version,
      });
    }
  } catch (error) {
    logger.error("Failed to resolve workspace path", {
      error: error instanceof Error ? error.message : String(error),
    });
    throw error;
  }

  return updates;
}
