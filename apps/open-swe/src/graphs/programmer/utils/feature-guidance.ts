import path from "node:path";
import {
  FeatureGraph,
  formatFeatureContext,
  loadFeatureGraph,
  testsForFeature,
} from "@openswe/shared/feature-graph";
import type {
  ArtifactCollection,
  ArtifactRef,
  FeatureNode,
} from "@openswe/shared/feature-graph/types";
import type { GraphConfig, GraphState } from "@openswe/shared/open-swe/types";
import { getActiveTask } from "@openswe/shared/open-swe/tasks";
import { createLogger, LogLevel } from "../../../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "ProgrammerFeatureGuidance");

const FEATURE_GRAPH_RELATIVE_PATH = path.join(
  "features",
  "graph",
  "graph.yaml",
);

let cachedGraph: FeatureGraph | undefined;
let cachedWorkspacePath: string | undefined;

async function loadProgrammerFeatureGraph(
  state: GraphState,
  config: GraphConfig,
): Promise<FeatureGraph | undefined> {
  const workspacePath =
    state.workspacePath ?? (config.configurable?.workspacePath as string | undefined);
  if (!workspacePath) {
    return undefined;
  }

  if (cachedGraph && cachedWorkspacePath === workspacePath) {
    return cachedGraph;
  }

  const graphPath = path.join(workspacePath, FEATURE_GRAPH_RELATIVE_PATH);
  try {
    const data = await loadFeatureGraph(graphPath);
    cachedWorkspacePath = workspacePath;
    cachedGraph = new FeatureGraph(data);
    return cachedGraph;
  } catch (error) {
    const details =
      error instanceof Error
        ? { name: error.name, message: error.message }
        : { error: String(error) };
    logger.warn("Unable to load feature graph", {
      workspacePath,
      ...details,
    });
    cachedWorkspacePath = undefined;
    cachedGraph = undefined;
    return undefined;
  }
}

type FlattenedArtifact = { key?: string; ref: ArtifactRef };

function flattenArtifactCollection(
  collection: ArtifactCollection | undefined,
): FlattenedArtifact[] {
  if (!collection) return [];
  if (Array.isArray(collection)) {
    return collection.map((ref) => ({ ref }));
  }

  return Object.entries(collection).flatMap(([key, ref]) => {
    if (Array.isArray(ref)) {
      return ref.map((value) => ({ key, ref: value }));
    }
    return [{ key, ref }];
  });
}

function artifactRefToString(ref: ArtifactRef, key?: string): string | undefined {
  if (typeof ref === "string") {
    return ref;
  }

  if (ref.path) return ref.path;
  if (ref.url) return ref.url;
  if (ref.name) return ref.name;
  if (ref.description) return ref.description;
  if (key) return key;
  if (ref.type) return ref.type;

  return undefined;
}

function normalizeIdentifier(value: string): string {
  return value.trim().toLowerCase();
}

function dedupeStrings(values: Iterable<string>): string[] {
  const seen = new Map<string, string>();
  for (const value of values) {
    const trimmed = value.trim();
    if (!trimmed) continue;
    const key = normalizeIdentifier(trimmed);
    if (!seen.has(key)) {
      seen.set(key, trimmed);
    }
  }
  return Array.from(seen.values());
}

function collectArtifactHints(features: FeatureNode[]): string[] {
  const candidates: string[] = [];
  for (const feature of features) {
    for (const entry of flattenArtifactCollection(feature.artifacts)) {
      const identifier = artifactRefToString(entry.ref, entry.key);
      if (identifier) {
        candidates.push(identifier);
      }
    }
  }
  return dedupeStrings(candidates);
}

function artifactRefsToStrings(refs: ArtifactRef[]): string[] {
  const identifiers = refs
    .map((ref) => artifactRefToString(ref))
    .filter((value): value is string => Boolean(value));
  return dedupeStrings(identifiers);
}

function isFeatureComplete(feature: FeatureNode): boolean {
  const status = feature.status?.toLowerCase() ?? "";
  return ["complete", "completed", "done", "shipped", "released"].some((token) =>
    status.includes(token),
  );
}

export type FeatureGuidance = {
  features: FeatureNode[];
  dependencies: FeatureNode[];
  scopeSummary?: string;
  artifactHints: string[];
  testHints: string[];
  pendingDependencies: FeatureNode[];
};

export async function collectFeatureGuidance(
  state: GraphState,
  config: GraphConfig,
): Promise<FeatureGuidance> {
  const features = state.features ?? [];
  const dependencies = state.featureDependencies ?? [];
  const scopeSummary = formatFeatureContext({ features, dependencies });

  const dedupedFeatures = new Map<string, FeatureNode>();
  for (const feature of [...features, ...dependencies]) {
    if (!feature) continue;
    if (!dedupedFeatures.has(feature.id)) {
      dedupedFeatures.set(feature.id, feature);
    }
  }
  const combinedFeatures = Array.from(dedupedFeatures.values());

  const artifactHints = collectArtifactHints(combinedFeatures);
  const pendingDependencies = dependencies.filter((feature) =>
    feature ? !isFeatureComplete(feature) : false,
  );

  const graph = await loadProgrammerFeatureGraph(state, config);
  let testHints: string[] = [];
  if (graph) {
    const activeTask = getActiveTask(state.taskPlan);
    const options = state.taskPlan
      ? { taskPlan: state.taskPlan, taskId: activeTask?.id }
      : undefined;

    const testCandidates: string[] = [];
    for (const feature of combinedFeatures) {
      const tests = testsForFeature(graph, feature.id, options);
      testCandidates.push(...artifactRefsToStrings(tests));
    }
    testHints = dedupeStrings(testCandidates);
  }

  return {
    features,
    dependencies,
    scopeSummary,
    artifactHints,
    testHints,
    pendingDependencies,
  };
}

export function formatFeatureGuidance(guidance: FeatureGuidance): string {
  const sections: string[] = [];
  const scope = guidance.scopeSummary?.trim();
  if (scope) {
    sections.push(scope);
  }

  if (guidance.artifactHints.length > 0) {
    sections.push(
      `<feature_artifacts>\nKey files or directories to inspect:\n${guidance.artifactHints
        .map((value) => `- ${value}`)
        .join("\n")}\n</feature_artifacts>`,
    );
  }

  if (guidance.testHints.length > 0) {
    sections.push(
      `<feature_tests>\nTests likely covering these features:\n${guidance.testHints
        .map((value) => `- ${value}`)
        .join("\n")}\n</feature_tests>`,
    );
  }

  if (guidance.pendingDependencies.length > 0) {
    const dependencyLines = guidance.pendingDependencies.map((dependency) => {
      const parts = [`- ${dependency.name ?? dependency.id} (${dependency.id})`];
      if (dependency.status) {
        parts.push(`— Status: ${dependency.status}`);
      }
      return parts.join(" ");
    });
    sections.push(
      `<feature_dependency_alert>\nDependent features still incomplete:\n${dependencyLines.join(
        "\n",
      )}\n</feature_dependency_alert>`,
    );
  }

  return sections.join("\n\n");
}
