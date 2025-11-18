import fs from "node:fs/promises";
import path from "node:path";
import { FeatureGraph, loadFeatureGraph } from "@openswe/shared/feature-graph";
import { FeatureGraphData } from "@openswe/shared/feature-graph/loader";
import {
  FeatureGraphFile,
  developmentProgressSchema,
  featureGraphFileSchema,
  featureNodeSchema,
} from "@openswe/shared/feature-graph/types";
import { writeFeatureGraphFile } from "@openswe/shared/feature-graph/writer";
import { getMessageContentString } from "@openswe/shared/messages";
import { LLMTask } from "@openswe/shared/open-swe/llm-task";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import { loadModel } from "../../../utils/llms/index.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "FeatureGraphGenerator");

const SYSTEM_PROMPT = `You generate feature graphs for software projects.
Each graph follows this JSON schema:
{
  "version": 1,
  "nodes": [
    {
      "id": "unique-id",
      "name": "Short title",
      "description": "Concise description of the feature or capability",
      "status": "current status such as proposed/active/deprecated",
      "development_progress": "To Do | In Progress | Completed",
      "group": "Optional area or domain label",
      "artifacts": ["Optional paths to relevant files or docs"]
    }
  ],
  "edges": [
    {
      "source": "upstream-feature-id",
      "target": "downstream-feature-id",
      "type": "dependency type (e.g. upstream, depends-on, relates-to)"
    }
  ],
  "artifacts": ["Optional shared artifact references"]
}

Rules:
- Always include a development_progress field on every node using one of: To Do, In Progress, Completed.
- Prefer 5-12 feature nodes that reflect the repository's actual functionality.
- Keep names short and descriptions focused on user-visible outcomes.
- Capture major dependencies between features using edges.
- Only return JSON. Do not wrap the response in markdown fences.`;

type WorkspaceContext = {
  readmeSnippet?: string;
  directories: string[];
};

async function collectWorkspaceContext(
  workspacePath: string,
): Promise<WorkspaceContext> {
  const directories: string[] = [];
  try {
    const entries = await fs.readdir(workspacePath, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory()) {
        directories.push(entry.name);
      }
    }
  } catch (error) {
    logger.warn("Unable to list workspace directories", {
      workspacePath,
      error: error instanceof Error ? error.message : String(error),
    });
  }

  let readmeSnippet: string | undefined;
  const readmePath = path.join(workspacePath, "README.md");
  try {
    const content = await fs.readFile(readmePath, "utf8");
    readmeSnippet = content.slice(0, 4_000);
  } catch {
    // optional
  }

  return { directories, readmeSnippet };
}

function coerceFeatureGraph(content: string): FeatureGraphFile {
  const cleaned = content
    .replace(/```json/gi, "")
    .replace(/```/g, "")
    .trim();

  let parsed: unknown;
  try {
    parsed = JSON.parse(cleaned);
  } catch (error) {
    throw new Error(
      `Unable to parse generated feature graph JSON: ${error instanceof Error ? error.message : String(error)}`,
    );
  }

  const normalized = featureGraphFileSchema.parse(parsed);
  const nodes: FeatureGraphFile["nodes"] = normalized.nodes.map((node) => {
    const parsedNode = featureNodeSchema.safeParse(node);
    if (parsedNode.success) {
      const validatedProgress = developmentProgressSchema.safeParse(
        parsedNode.data.development_progress,
      ).success
        ? parsedNode.data.development_progress
        : "To Do";

      return {
        ...parsedNode.data,
        development_progress: validatedProgress,
      };
    }
    return node;
  });

  return { ...normalized, nodes };
}

type GenerationResult = {
  graphData: FeatureGraphData;
  graphFile: FeatureGraphFile;
  featureGraph: FeatureGraph;
  activeFeatureIds: string[];
};

export async function generateFeatureGraphForWorkspace({
  workspacePath,
  graphPath,
  config,
}: {
  workspacePath: string;
  graphPath: string;
  config: GraphConfig;
}): Promise<GenerationResult> {
  const context = await collectWorkspaceContext(workspacePath);

  const model = await loadModel(config, LLMTask.PLANNER);
  const response = await model.invoke([
    { role: "system", content: SYSTEM_PROMPT },
    {
      role: "user",
      content: [
        "Generate a feature graph for the workspace using the schema above.",
        `Workspace path: ${workspacePath}`,
        context.directories.length
          ? `Top-level directories: ${context.directories.join(", ")}`
          : "Top-level directories unknown.",
        context.readmeSnippet
          ? `README.md excerpt:\n${context.readmeSnippet}`
          : "No README.md excerpt available.",
      ].join("\n"),
    },
  ]);

  const content = getMessageContentString(response.content);
  const graphFile = coerceFeatureGraph(content);

  await fs.mkdir(path.dirname(graphPath), { recursive: true });
  await writeFeatureGraphFile({
    graphPath,
    version: graphFile.version,
    nodes: graphFile.nodes,
    edges: graphFile.edges,
    artifacts: graphFile.artifacts,
  });

  const graphData = await loadFeatureGraph(graphPath);
  const featureGraph = new FeatureGraph(graphData);
  const activeFeatureIds = featureGraph.listFeatures().map((node) => node.id);

  logger.info("Generated feature graph", {
    workspacePath,
    graphPath,
    featureCount: activeFeatureIds.length,
  });

  return { graphData, graphFile, featureGraph, activeFeatureIds };
}
