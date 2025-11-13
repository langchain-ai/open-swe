/* eslint-disable no-console */
import { readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

import { FeatureGraph } from "@openswe/shared/feature-graph/graph";
import {
  loadFeatureGraph,
  type FeatureGraphData,
} from "@openswe/shared/feature-graph/loader";
import {
  FeatureEdge,
  featureGraphFileSchema,
} from "@openswe/shared/feature-graph/types";

const DISALLOWED_CYCLE_EDGE_TYPES = new Set(["REQUIRES", "COMPOSED_OF"]);

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DEFAULT_GRAPH_PATH = path.resolve(
  __dirname,
  "../../..",
  "features",
  "graph",
  "graph.yaml",
);

const normalizeError = (error: unknown): string => {
  if (error instanceof Error) return error.message;
  return String(error);
};

const canonicalizeCycle = (cycle: string[]): string => {
  if (cycle.length <= 1) return cycle.join(" -> ");
  const nodes = cycle.slice(0, -1);
  let best: string | undefined;

  for (let index = 0; index < nodes.length; index += 1) {
    const rotated = nodes.slice(index).concat(nodes.slice(0, index));
    const rotatedWithReturn = [...rotated, rotated[0]];
    const candidate = rotatedWithReturn.join(" -> ");
    if (!best || candidate < best) {
      best = candidate;
    }
  }

  return best ?? cycle.join(" -> ");
};

const findOrphanNodes = (graph: FeatureGraph): string[] =>
  graph
    .listFeatures()
    .filter((node) =>
      graph.getEdgesFrom(node.id).length === 0 &&
      graph.getEdgesInto(node.id).length === 0,
    )
    .map((node) => node.id);

const buildCycleAdjacency = (
  edges: FeatureEdge[],
  restrictedTypes: ReadonlySet<string>,
): Map<string, Set<string>> => {
  const adjacency = new Map<string, Set<string>>();

  for (const edge of edges) {
    if (!restrictedTypes.has(edge.type)) continue;

    if (!adjacency.has(edge.source)) {
      adjacency.set(edge.source, new Set());
    }
    adjacency.get(edge.source)!.add(edge.target);

    if (!adjacency.has(edge.target)) {
      adjacency.set(edge.target, new Set());
    }
  }

  return adjacency;
};

const detectCycles = (adjacency: Map<string, Set<string>>): string[] => {
  const visited = new Set<string>();
  const stack = new Set<string>();
  const stackIndex = new Map<string, number>();
  const path: string[] = [];
  const seen = new Set<string>();
  const cycles: string[] = [];

  const visit = (node: string) => {
    visited.add(node);
    stack.add(node);
    stackIndex.set(node, path.length);
    path.push(node);

    const neighbors = adjacency.get(node);
    if (neighbors) {
      for (const neighbor of neighbors) {
        if (stack.has(neighbor)) {
          const startIndex = stackIndex.get(neighbor);
          if (startIndex !== undefined) {
            const cyclePath = path.slice(startIndex);
            cyclePath.push(neighbor);
            const canonical = canonicalizeCycle(cyclePath);
            if (!seen.has(canonical)) {
              seen.add(canonical);
              cycles.push(canonical);
            }
            cyclePath.pop();
          }
          continue;
        }

        if (!visited.has(neighbor)) {
          visit(neighbor);
        }
      }
    }

    path.pop();
    stack.delete(node);
    stackIndex.delete(node);
  };

  for (const node of adjacency.keys()) {
    if (!visited.has(node)) {
      visit(node);
    }
  }

  cycles.sort((a, b) => a.localeCompare(b));
  return cycles;
};

const validateSchema = async (graphPath: string): Promise<void> => {
  const fileContent = await readFile(graphPath, "utf8");
  const parsed = YAML.parse(fileContent, { prettyErrors: true });
  featureGraphFileSchema.parse(parsed);
};

const main = async (): Promise<void> => {
  const [, , targetPath] = process.argv;
  const graphPath = targetPath
    ? path.resolve(process.cwd(), targetPath)
    : DEFAULT_GRAPH_PATH;

  console.log(`Validating feature graph at ${graphPath}`);

  try {
    await validateSchema(graphPath);
  } catch (error) {
    console.error(`Schema validation failed: ${normalizeError(error)}`);
    process.exitCode = 1;
    return;
  }

  let data: FeatureGraphData;
  try {
    data = await loadFeatureGraph(graphPath);
  } catch (error) {
    console.error(`Failed to load feature graph: ${normalizeError(error)}`);
    process.exitCode = 1;
    return;
  }

  const graph = new FeatureGraph(data);
  const issues: string[] = [];

  const nodes = graph.listFeatures();
  if (nodes.length > 1) {
    const orphanNodes = findOrphanNodes(graph);
    if (orphanNodes.length > 0) {
      issues.push(
        `Found ${orphanNodes.length} orphan node(s): ${orphanNodes.join(", ")}`,
      );
    }
  }

  const adjacency = buildCycleAdjacency(graph.listEdges(), DISALLOWED_CYCLE_EDGE_TYPES);
  const cycles = detectCycles(adjacency);
  if (cycles.length > 0) {
    issues.push(
      `Detected ${cycles.length} restricted cycle(s):\n  - ${cycles.join(
        "\n  - ",
      )}`,
    );
  }

  if (issues.length > 0) {
    console.error("Feature graph validation failed:");
    for (const issue of issues) {
      console.error(`  • ${issue}`);
    }
    process.exitCode = 1;
    return;
  }

  console.log("Feature graph validation passed");
};

main().catch((error) => {
  console.error(`Unexpected validation error: ${normalizeError(error)}`);
  process.exitCode = 1;
});
