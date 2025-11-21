import { randomUUID } from "crypto";
import type { Hono } from "hono";
import type { ContentfulStatusCode } from "hono/utils/http-status";
import { StreamMode } from "@langchain/langgraph-sdk";
import { FeatureGraph } from "@openswe/shared/feature-graph/graph";
import type {
  ArtifactCollection,
  ArtifactRef,
  FeatureEdge,
  FeatureNode,
} from "@openswe/shared/feature-graph/types";
import {
  LOCAL_MODE_HEADER,
  OPEN_SWE_STREAM_MODE,
  PLANNER_GRAPH_ID,
} from "@openswe/shared/constants";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import type { PlannerGraphUpdate } from "@openswe/shared/open-swe/planner/types";
import { getCustomConfigurableFields } from "@openswe/shared/open-swe/utils/config";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { resolveInsideRoot } from "./run.js";
import { generateFeatureGraphForWorkspace } from "../../graphs/manager/utils/generate-feature-graph.js";
import { createLangGraphClient } from "../../utils/langgraph-client.js";

const logger = createLogger(LogLevel.INFO, "FeatureGraphRoute");

type GenerateRequestBody = {
  workspaceAbsPath?: unknown;
  configurable?: Record<string, unknown>;
  prompt?: unknown;
};

type DevelopRequestBody = {
  thread_id?: unknown;
  threadId?: unknown;
  feature_id?: unknown;
  featureId?: unknown;
};

export function registerFeatureGraphRoute(app: Hono) {
  app.post("/feature-graph/generate", async (ctx) => {
    let body: GenerateRequestBody;

    try {
      body = await ctx.req.json<GenerateRequestBody>();
    } catch (error) {
      logger.error("Invalid JSON payload for feature graph generation", {
        error: error instanceof Error ? error.message : String(error),
      });
      return ctx.json(
        { error: "Invalid JSON payload." },
        400 as ContentfulStatusCode,
      );
    }

    const workspaceAbsPath =
      typeof body.workspaceAbsPath === "string" ? body.workspaceAbsPath : undefined;
    const prompt =
      typeof body.prompt === "string" && body.prompt.trim()
        ? body.prompt.trim()
        : undefined;

    if (!workspaceAbsPath) {
      return ctx.json(
        { error: "workspaceAbsPath is required" },
        400 as ContentfulStatusCode,
      );
    }

    try {
      const resolvedWorkspaceAbsPath = resolveInsideRoot(workspaceAbsPath);
      const config: GraphConfig = {
        configurable: {
          workspacePath: resolvedWorkspaceAbsPath,
          ...(body.configurable ?? {}),
        },
      } as GraphConfig;

      const graphPath = `${resolvedWorkspaceAbsPath}/features/graph/graph.yaml`;
      const generated = await generateFeatureGraphForWorkspace({
        workspacePath: resolvedWorkspaceAbsPath,
        graphPath,
        config,
        prompt,
      });

      return ctx.json({
        featureGraph: generated.graphFile,
        activeFeatureIds: generated.activeFeatureIds,
      });
    } catch (error) {
      logger.error("Failed to generate feature graph", {
        error: error instanceof Error ? error.message : String(error),
      });
      return ctx.json(
        { error: "Failed to generate feature graph." },
        500 as ContentfulStatusCode,
      );
    }
  });

  app.post("/feature-graph/develop", async (ctx) => {
    const body = await ctx.req.json<DevelopRequestBody>().catch((error) => {
      logger.error("Invalid JSON payload for feature graph develop", {
        error: error instanceof Error ? error.message : String(error),
      });
      return null;
    });

    const threadId = resolveThreadId(body);
    const featureId = resolveFeatureId(body);

    if (!threadId) {
      return ctx.json(
        { error: "thread_id is required" },
        400 as ContentfulStatusCode,
      );
    }

    if (!featureId) {
      return ctx.json(
        { error: "feature_id is required" },
        400 as ContentfulStatusCode,
      );
    }

    const client = createLangGraphClient({
      defaultHeaders:
        process.env.OPEN_SWE_LOCAL_MODE === "true"
          ? { [LOCAL_MODE_HEADER]: "true" }
          : undefined,
    });

    const managerThreadState = await client.threads
      .getState<ManagerGraphState>(threadId)
      .catch((error) => {
        logger.error("Failed to load manager state for feature develop", {
          error: error instanceof Error ? error.message : String(error),
        });
        return null;
      });

    if (!managerThreadState?.values) {
      return ctx.json(
        { error: "Manager state not found for thread" },
        404 as ContentfulStatusCode,
      );
    }

    const featureGraph = coerceFeatureGraph(managerThreadState.values.featureGraph);
    if (!featureGraph) {
      return ctx.json(
        { error: "Feature graph not available for thread" },
        404 as ContentfulStatusCode,
      );
    }

    const selectedFeature = featureGraph.getFeature(featureId);
    if (!selectedFeature) {
      return ctx.json(
        { error: "Feature not found in manager state" },
        404 as ContentfulStatusCode,
      );
    }

    const featureDependencies = featureGraph
      .getNeighbors(featureId, "both")
      .filter((neighbor) => neighbor.id !== selectedFeature.id);

    const plannerThreadId =
      managerThreadState.values.plannerSession?.threadId ?? randomUUID();

    const plannerRunInput = buildPlannerRunInput({
      managerState: managerThreadState.values,
      featureId,
      selectedFeature,
      featureDependencies,
    });

    let run;
    try {
      run = await client.runs.create(plannerThreadId, PLANNER_GRAPH_ID, {
        input: plannerRunInput,
        config: {
          recursion_limit: 400,
          configurable: {
            ...getCustomConfigurableFields({
              configurable: (managerThreadState.metadata?.configurable ?? {}) as
                | GraphConfig["configurable"]
                | undefined,
            } as GraphConfig),
            ...(managerThreadState.values.workspacePath
              ? { workspacePath: managerThreadState.values.workspacePath }
              : {}),
            ...(process.env.OPEN_SWE_LOCAL_MODE === "true"
              ? { [LOCAL_MODE_HEADER]: "true" }
              : {}),
          },
        },
        ifNotExists: "create",
        streamResumable: true,
        streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
      });
    } catch (error) {
      logger.error("Failed to create planner run from feature develop", {
        error: error instanceof Error ? error.message : String(error),
      });
      return ctx.json(
        { error: "Failed to start planner run" },
        500 as ContentfulStatusCode,
      );
    }

    if (!run) {
      return ctx.json(
        { error: "Failed to start planner run" },
        500 as ContentfulStatusCode,
      );
    }

    const updatedManagerState: ManagerGraphUpdate = {
      plannerSession: {
        threadId: plannerThreadId,
        runId: run.run_id,
      },
      activeFeatureIds: [featureId],
    };

    await client.threads
      .updateState<ManagerGraphState>(threadId, {
        values: {
          ...managerThreadState.values,
          ...updatedManagerState,
        },
        asNode: "start-planner",
      })
      .catch((error) => {
        logger.error("Failed to update manager state after feature develop", {
          error: error instanceof Error ? error.message : String(error),
        });
      });

    return ctx.json({
      planner_thread_id: plannerThreadId,
      run_id: run.run_id,
    });
  });
}

function resolveThreadId(body: DevelopRequestBody | null): string | null {
  const candidate = body?.thread_id ?? body?.threadId;
  if (typeof candidate === "string" && candidate.trim()) {
    return candidate.trim();
  }
  return null;
}

function resolveFeatureId(body: DevelopRequestBody | null): string | null {
  const candidate = body?.feature_id ?? body?.featureId;
  if (typeof candidate === "string" && candidate.trim()) {
    return candidate.trim();
  }
  return null;
}

function buildPlannerRunInput({
  managerState,
  featureId,
  selectedFeature,
  featureDependencies,
}: {
  managerState: ManagerGraphState;
  featureId: string;
  selectedFeature: FeatureNode;
  featureDependencies: FeatureNode[];
}): PlannerGraphUpdate {
  return {
    issueId: managerState.issueId,
    targetRepository: managerState.targetRepository,
    taskPlan: managerState.taskPlan,
    branchName: managerState.branchName,
    autoAcceptPlan: managerState.autoAcceptPlan,
    workspacePath: managerState.workspacePath,
    activeFeatureIds: [featureId],
    features: [selectedFeature, ...featureDependencies],
    featureDependencies,
    programmerSession: managerState.programmerSession,
    messages: managerState.messages,
  } satisfies PlannerGraphUpdate;
}

function coerceFeatureGraph(value: unknown): FeatureGraph | null {
  if (!value) return null;

  if (value instanceof FeatureGraph) {
    return value;
  }

  const payload = extractGraphPayload(value);
  if (!payload) return null;

  const nodes = coerceFeatureNodeMap(payload.nodes);
  if (!nodes) return null;

  const edges = coerceFeatureEdges(payload.edges);
  const artifacts = coerceArtifactCollection(payload.artifacts);
  const version = typeof payload.version === "number" ? payload.version : 1;

  try {
    return new FeatureGraph({ version, nodes, edges, artifacts });
  } catch {
    return null;
  }
}

type SerializedFeatureGraph = {
  version?: number;
  nodes?: unknown;
  edges?: unknown;
  artifacts?: unknown;
};

function extractGraphPayload(value: unknown): SerializedFeatureGraph | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  if (
    "data" in value &&
    typeof (value as { data?: unknown }).data === "object" &&
    (value as { data?: unknown }).data
  ) {
    return (value as { data: SerializedFeatureGraph }).data;
  }

  return value as SerializedFeatureGraph;
}

function coerceFeatureNodeMap(value: unknown): Map<string, FeatureNode> | null {
  if (!value) return null;

  const map = new Map<string, FeatureNode>();

  if (value instanceof Map) {
    for (const [, node] of value) {
      const normalized = coerceFeatureNode(node);
      if (normalized) {
        map.set(normalized.id, normalized);
      }
    }
  } else if (Array.isArray(value)) {
    for (const entry of value) {
      if (Array.isArray(entry) && entry.length >= 2) {
        const [, node] = entry;
        const normalized = coerceFeatureNode(node);
        if (normalized) {
          map.set(normalized.id, normalized);
        }
        continue;
      }

      const normalized = coerceFeatureNode(entry);
      if (normalized) {
        map.set(normalized.id, normalized);
      }
    }
  } else if (isPlainObject(value)) {
    for (const candidate of Object.values(value)) {
      const normalized = coerceFeatureNode(candidate);
      if (normalized) {
        map.set(normalized.id, normalized);
      }
    }
  }

  return map.size > 0 ? map : null;
}

function coerceFeatureNode(value: unknown): FeatureNode | null {
  if (!isPlainObject(value)) return null;

  const { id, name, description, status } = value as FeatureNode;

  if (
    typeof id !== "string" ||
    typeof name !== "string" ||
    typeof description !== "string" ||
    typeof status !== "string"
  ) {
    return null;
  }

  const node: FeatureNode = { id, name, description, status };

  if ("group" in value && typeof (value as FeatureNode).group === "string") {
    node.group = (value as FeatureNode).group;
  }

  if ("metadata" in value && isPlainObject((value as FeatureNode).metadata)) {
    node.metadata = (value as FeatureNode).metadata as Record<string, unknown>;
  }

  if ("artifacts" in value) {
    const artifacts = coerceArtifactCollection(
      (value as FeatureNode).artifacts as unknown,
    );
    if (artifacts) {
      node.artifacts = artifacts;
    }
  }

  return node;
}

function coerceFeatureEdges(value: unknown): FeatureEdge[] {
  if (!Array.isArray(value)) return [];

  const edges: FeatureEdge[] = [];
  for (const entry of value) {
    if (!isPlainObject(entry)) continue;
    const { source, target, type } = entry as FeatureEdge;
    if (
      typeof source === "string" &&
      typeof target === "string" &&
      typeof type === "string"
    ) {
      const edge: FeatureEdge = { source, target, type };
      if (
        "metadata" in entry &&
        isPlainObject((entry as FeatureEdge).metadata)
      ) {
        edge.metadata = (entry as FeatureEdge).metadata as Record<string, unknown>;
      }
      edges.push(edge);
    }
  }

  return edges;
}

function coerceArtifactCollection(
  value: unknown,
): ArtifactCollection | undefined {
  if (!value) return undefined;

  if (Array.isArray(value)) {
    const artifacts: ArtifactRef[] = [];
    for (const entry of value) {
      const artifact = coerceArtifactRef(entry);
      if (artifact) artifacts.push(artifact);
    }
    return artifacts.length > 0 ? artifacts : undefined;
  }

  if (isPlainObject(value)) {
    const artifacts: Record<string, ArtifactRef> = {};
    for (const [key, entry] of Object.entries(value)) {
      const artifact = coerceArtifactRef(entry);
      if (artifact) {
        artifacts[key] = artifact;
      }
    }
    return Object.keys(artifacts).length > 0 ? artifacts : undefined;
  }

  return undefined;
}

function coerceArtifactRef(value: unknown): ArtifactRef | null {
  if (typeof value === "string") {
    return value.trim() ? value : null;
  }

  if (isArtifactRefObject(value)) {
    return value;
  }

  return null;
}

function isArtifactRefObject(value: unknown): value is Exclude<ArtifactRef, string> {
  if (!isPlainObject(value)) return false;

  const artifact = value as Exclude<ArtifactRef, string>;

  return (
    typeof artifact.path === "string" ||
    typeof artifact.url === "string" ||
    typeof artifact.name === "string" ||
    typeof artifact.description === "string" ||
    typeof artifact.type === "string" ||
    Boolean(artifact.metadata && typeof artifact.metadata === "object")
  );
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}
