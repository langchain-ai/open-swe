import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import { FeatureGraph, loadFeatureGraph } from "@openswe/shared/feature-graph";
import type { FeatureGraphData } from "@openswe/shared/feature-graph/loader";
import {
  resolveWorkspacePath,
  resolvePathInsideWorkspace,
} from "../../../utils/workspace.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { generateFeatureGraphForWorkspace } from "../utils/generate-feature-graph.js";

let featureGraphGenerator = generateFeatureGraphForWorkspace;

export function setFeatureGraphGenerator(
  generator: typeof generateFeatureGraphForWorkspace,
) {
  featureGraphGenerator = generator;
}

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

const mergeActiveFeatureIds = (
  generatedIds: string[] | undefined,
  existingIds: string[] | undefined,
  graph: FeatureGraph,
): string[] | undefined => {
  const collected = new Set<string>();

  for (const id of generatedIds ?? []) {
    const trimmed = id.trim();
    if (trimmed.length > 0) {
      collected.add(trimmed);
    }
  }

  for (const id of existingIds ?? []) {
    const trimmed = id.trim();
    if (trimmed.length > 0) {
      collected.add(trimmed);
    }
  }

  if (collected.size === 0) {
    const fromGraph = graph
      .listFeatures()
      .map((feature) => feature.id.trim())
      .filter((id) => id.length > 0);
    fromGraph.forEach((id) => collected.add(id));
  }

  return collected.size > 0 ? Array.from(collected) : undefined;
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
      let graphData: FeatureGraphData | undefined;
      let activeFeatureIds: string[] | undefined;

      const shouldGenerate =
        process.env.OPEN_SWE_DISABLE_FEATURE_GRAPH_GENERATION !== "true";

      if (!(await fileExists(graphPath)) && shouldGenerate) {
        try {
          const generated = await featureGraphGenerator({
            workspacePath,
            graphPath,
            config: _config,
          });
          graphData = generated.graphData;
          activeFeatureIds = generated.activeFeatureIds;
          logger.info("Generated feature graph for workspace", { graphPath });
        } catch (generationError) {
          logger.warn("Unable to generate workspace feature graph", {
            error:
              generationError instanceof Error
                ? generationError.message
                : String(generationError),
          });
          if (await fileExists(DEFAULT_FEATURE_GRAPH_PATH)) {
            graphSourcePath = DEFAULT_FEATURE_GRAPH_PATH;
            logger.info(
              "Workspace feature graph missing, using default graph",
              {
                workspaceGraphPath: graphPath,
                defaultGraphPath: graphSourcePath,
              },
            );
          } else {
            throw new Error(
              `Feature graph not found at ${graphPath}. Add a features/graph/graph.yaml file to your workspace.`,
            );
          }
        }
      }

      if (!(await fileExists(graphSourcePath))) {
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
      const data = graphData ?? (await loadFeatureGraph(graphSourcePath));
      updates.featureGraph = new FeatureGraph(data);
      updates.activeFeatureIds = mergeActiveFeatureIds(
        activeFeatureIds,
        state.activeFeatureIds,
        updates.featureGraph,
      );
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
