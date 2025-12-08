import type { Dirent } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import {
  listFeaturesFromGraph,
  loadFeatureGraph,
} from "@openswe/shared/feature-graph";
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
- Use the provided repository tree (up to 7 layers) and README excerpt to ground each feature in the existing codebase.
- When a user request is provided, treat it as the authoritative requirements and ensure every node aligns with it.
- Each feature description must read like a mini design spec with explicit implementation guidance for planners/programmers.
- If key questions remain unanswered, dedicate nodes to clarifying those unknowns so the chat agent can follow up.
- Only return JSON. Do not wrap the response in markdown fences.`;

type WorkspaceContext = {
  readmeSnippet?: string;
  directories: string[];
  codeTree?: string;
};

type GenerationResult = {
  graphData: FeatureGraphData;
  graphFile: FeatureGraphFile;
  activeFeatureIds: string[];
};

const MAX_TREE_DEPTH = 7;
const MAX_TREE_ENTRIES = 400;
const IGNORED_DIRECTORIES = new Set([
  ".git",
  "node_modules",
  ".next",
  "build",
  "dist",
  ".turbo",
]);

async function buildDirectoryTree(
  workspacePath: string,
): Promise<string | undefined> {
  const lines: string[] = [];
  let entryCount = 0;

  const walk = async (currentPath: string, depth: number) => {
    if (depth >= MAX_TREE_DEPTH || entryCount >= MAX_TREE_ENTRIES) {
      return;
    }

  let entries: Dirent[];
    try {
      entries = await fs.readdir(currentPath, { withFileTypes: true });
    } catch {
      return;
    }

    entries = entries
      .filter((entry) => !entry.name.startsWith(".") || depth === 0)
      .filter((entry) =>
        entry.isDirectory() ? !IGNORED_DIRECTORIES.has(entry.name) : true,
      )
      .sort((a, b) => {
        if (a.isDirectory() !== b.isDirectory()) {
          return a.isDirectory() ? -1 : 1;
        }
        return a.name.localeCompare(b.name);
      });

    for (const entry of entries) {
      if (entryCount >= MAX_TREE_ENTRIES) {
        break;
      }

      entryCount += 1;
      const prefix = depth > 0 ? "  ".repeat(depth) : "";
      const label = `${prefix}${entry.name}${entry.isDirectory() ? "/" : ""}`;
      lines.push(label);

      if (entry.isDirectory()) {
        await walk(path.join(currentPath, entry.name), depth + 1);
      }

      if (entryCount >= MAX_TREE_ENTRIES) {
        break;
      }
    }
  };

  await walk(workspacePath, 0);

  return lines.length ? lines.join("\n") : undefined;
}

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

  const codeTree = await buildDirectoryTree(workspacePath);

  return { directories, readmeSnippet, codeTree };
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

async function loadExistingGraph(graphPath: string): Promise<GenerationResult | null> {
  const graphExists = await fs
    .access(graphPath)
    .then(() => true)
    .catch(() => false);

  if (!graphExists) {
    return null;
  }

  const graphData = await loadFeatureGraph(graphPath);
  const activeFeatureIds = listFeaturesFromGraph(graphData, {
    activeFeatureIds: graphData.nodes.keys(),
  }).map((node) => node.id);
  const graphFile: FeatureGraphFile = {
    version: graphData.version,
    nodes: Array.from(graphData.nodes.values()),
    edges: graphData.edges,
    artifacts: graphData.artifacts,
  };

  logger.info("Loaded existing feature graph file", {
    graphPath,
    featureCount: activeFeatureIds.length,
  });

  return { graphData, graphFile, activeFeatureIds };
}

export async function generateFeatureGraphForWorkspace({
  workspacePath,
  graphPath,
  config,
  prompt,
}: {
  workspacePath: string;
  graphPath: string;
  config: GraphConfig;
  prompt?: string;
}): Promise<GenerationResult> {
  if (process.env.OPEN_SWE_LOCAL_MODE === "true") {
    const existingGraph = await loadExistingGraph(graphPath);
    if (existingGraph) {
      return existingGraph;
    }

    logger.warn("Local mode enabled but no existing feature graph found", {
      workspacePath,
      graphPath,
    });
  }

  const context = await collectWorkspaceContext(workspacePath);

  const model = await loadModel(config, LLMTask.PLANNER);
  const response = await model.invoke([
    { role: "system", content: SYSTEM_PROMPT },
    {
      role: "user",
      content: [
        "Generate a feature graph for the workspace using the schema above.",
        `Workspace path: ${workspacePath}`,
        prompt
          ? `Primary user request:\n${prompt}`
          : "Primary user request not provided. Infer improvements based on the repository.",
        context.directories.length
          ? `Top-level directories: ${context.directories.join(", ")}`
          : "Top-level directories unknown.",
        context.codeTree
          ? `Repository structure (max ${MAX_TREE_DEPTH} levels):\n${context.codeTree}`
          : "Repository structure unavailable.",
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
  const activeFeatureIds = listFeaturesFromGraph(graphData, {
    activeFeatureIds: graphData.nodes.keys(),
  }).map((node) => node.id);

  logger.info("Generated feature graph", {
    workspacePath,
    graphPath,
    featureCount: activeFeatureIds.length,
  });

  return { graphData, graphFile, activeFeatureIds };
}
