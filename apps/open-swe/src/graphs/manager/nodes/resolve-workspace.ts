import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
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

const moduleDir = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_FEATURE_GRAPH_PATH = path.resolve(
  moduleDir,
  "../../../../../../",
  FEATURE_GRAPH_RELATIVE_PATH,
);

const fileExists = async (filePath: string): Promise<boolean> => {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
};

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
      let graphSourcePath = graphPath;

      if (!(await fileExists(graphPath))) {
        if (await fileExists(DEFAULT_FEATURE_GRAPH_PATH)) {
          graphSourcePath = DEFAULT_FEATURE_GRAPH_PATH;
          logger.info("Workspace feature graph missing, using default graph", {
            workspaceGraphPath: graphPath,
            defaultGraphPath: graphSourcePath,
          });
        } else {
          throw new Error(
            `Feature graph not found at ${graphPath}. Add a features/graph/graph.yaml file to your workspace.`,
          );
        }
      }

      logger.info("Loading feature graph", { graphPath: graphSourcePath });
      const data = await loadFeatureGraph(graphSourcePath);
      updates.featureGraph = new FeatureGraph(data);
      logger.info("Loaded feature graph", {
        graphPath: graphSourcePath,
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
