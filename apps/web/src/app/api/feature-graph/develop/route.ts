import { randomUUID } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import { Client, StreamMode } from "@langchain/langgraph-sdk";
import {
  LOCAL_MODE_HEADER,
  MANAGER_GRAPH_ID,
  OPEN_SWE_STREAM_MODE,
  PLANNER_GRAPH_ID,
} from "@openswe/shared/constants";
import {
  clarifyFeatureDescription,
  reconcileFeatureGraph,
  type FeatureGraph,
  type FeatureNode,
} from "@openswe/shared/feature-graph";
import type {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import type { PlannerGraphUpdate } from "@openswe/shared/open-swe/planner/types";
import type { GraphConfig } from "@openswe/shared/open-swe/types";
import { getCustomConfigurableFields } from "@openswe/shared/open-swe/utils/config";
import { coerceFeatureGraph } from "@/lib/coerce-feature-graph";

type ResolvedFeatureGraph = {
  featureGraph: FeatureGraph;
  managerState: ManagerGraphState;
};

class ApiConfigError extends Error {}
class ServiceUnavailableError extends Error {}

function resolveApiUrl(): string {
  const apiUrl =
    process.env.LANGGRAPH_API_URL?.trim() ??
    process.env.NEXT_PUBLIC_API_URL?.trim();

  if (!apiUrl) {
    throw new ApiConfigError(
      "LangGraph API URL not configured. Set LANGGRAPH_API_URL or NEXT_PUBLIC_API_URL.",
    );
  }

  try {
    new URL(apiUrl);
  } catch {
    throw new ApiConfigError(`Invalid LangGraph API URL: ${apiUrl}`);
  }

  return apiUrl;
}

function resolveThreadId(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  return null;
}

function getFeatureDependencies(graph: FeatureGraph, featureId: string): FeatureNode[] {
  const seen = new Set<string>([featureId]);
  const dependencies: FeatureNode[] = [];

  for (const neighbor of graph.getNeighbors(featureId, "both")) {
    if (seen.has(neighbor.id)) continue;
    seen.add(neighbor.id);
    dependencies.push(neighbor);
  }

  return dependencies;
}

async function startFeatureGraphGeneration({
  client,
  threadId,
}: {
  client: Client;
  threadId: string;
}) {
  await client.runs.create(threadId, MANAGER_GRAPH_ID, {
    input: {
      action: "generate_feature_graph",
      messages: [
        {
          role: "user",
          content: "Requesting feature graph generation",
          additional_kwargs: {
            phase: "design",
            requestSource: "open-swe",
          },
        },
      ],
    },
    config: {
      configurable: {
        phase: "design",
      },
    },
    ifNotExists: "create",
  });
}

async function waitForFeatureGraph({
  client,
  threadId,
  attempts = 5,
  delayMs = 1000,
}: {
  client: Client;
  threadId: string;
  attempts?: number;
  delayMs?: number;
}): Promise<ResolvedFeatureGraph | null> {
  for (let index = 0; index < attempts; index += 1) {
    const managerThreadState = await client.threads
      .getState<ManagerGraphState>(threadId)
      .catch(() => null);

    const featureGraph = coerceFeatureGraph(managerThreadState?.values?.featureGraph);
    if (managerThreadState?.values && featureGraph) {
      return {
        featureGraph,
        managerState: managerThreadState.values,
      } satisfies ResolvedFeatureGraph;
    }

    await new Promise((resolve) => setTimeout(resolve, delayMs));
  }

  return null;
}

async function ensureFeatureGraph({
  client,
  threadId,
  managerState,
}: {
  client: Client;
  threadId: string;
  managerState: ManagerGraphState;
}): Promise<ResolvedFeatureGraph> {
  const existingGraph = coerceFeatureGraph(managerState.featureGraph);
  if (existingGraph) {
    return { featureGraph: existingGraph, managerState } satisfies ResolvedFeatureGraph;
  }

  try {
    await startFeatureGraphGeneration({ client, threadId });
  } catch (error) {
    const message =
      error instanceof Error && error.message
        ? error.message
        : "Failed to initiate feature graph generation";
    throw new ServiceUnavailableError(message);
  }

  const refreshed = await waitForFeatureGraph({ client, threadId });
  if (!refreshed) {
    throw new Error("Feature graph could not be generated for thread");
  }

  return refreshed;
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  try {
    const body = await request.json();
    const threadId =
      resolveThreadId(body?.thread_id) ?? resolveThreadId(body?.threadId);
    const featureId =
      typeof body?.feature_id === "string"
        ? body.feature_id.trim()
        : typeof body?.featureId === "string"
          ? body.featureId.trim()
          : "";

    if (!threadId) {
      return NextResponse.json(
        { error: "thread_id is required" },
        { status: 400 },
      );
    }

    if (!featureId) {
      return NextResponse.json(
        { error: "feature_id is required" },
        { status: 400 },
      );
    }

    const client = new Client({
      apiUrl: resolveApiUrl(),
      defaultHeaders:
        process.env.OPEN_SWE_LOCAL_MODE === "true"
          ? { [LOCAL_MODE_HEADER]: "true" }
          : undefined,
    });

    const managerThreadState = await client.threads
      .getState<ManagerGraphState>(threadId)
      .catch((error) => {
        const message =
          error instanceof Error && error.message
            ? error.message
            : "LangGraph backend unreachable";
        throw new ServiceUnavailableError(
          `LangGraph backend unreachable: ${message}`,
        );
      });

    if (!managerThreadState?.values) {
      return NextResponse.json(
        { error: "Manager state not found for thread" },
        { status: 404 },
      );
    }

    const { featureGraph, managerState } = await ensureFeatureGraph({
      client,
      threadId,
      managerState: managerThreadState.values,
    });

    let { graph: reconciledGraph, dependencyMap } =
      reconcileFeatureGraph(featureGraph);

    const existingPlannerSession = managerState.plannerSession;
    const plannerThreadId =
      existingPlannerSession?.threadId ?? randomUUID();

    let selectedFeature = reconciledGraph.getFeature(featureId);

    if (!selectedFeature) {
      const refreshedState = await client.threads
        .getState<ManagerGraphState>(threadId)
        .catch(() => null);
      const refreshedGraph = coerceFeatureGraph(
        refreshedState?.values?.featureGraph,
      );

      if (refreshedGraph && refreshedState?.values) {
        ({ graph: reconciledGraph, dependencyMap } =
          reconcileFeatureGraph(refreshedGraph));
        selectedFeature = reconciledGraph.getFeature(featureId);
      }
    }

    if (!selectedFeature) {
      return NextResponse.json(
        {
          error: `Feature ${featureId} not found in feature graph after reconciliation`,
        },
        { status: 404 },
      );
    }

    const featureDependencies = getFeatureDependencies(
      reconciledGraph,
      featureId,
    );

    const plannerRunInput: PlannerGraphUpdate = {
      issueId: managerState.issueId,
      targetRepository: managerState.targetRepository,
      taskPlan: managerState.taskPlan,
      branchName: managerState.branchName,
      autoAcceptPlan: managerState.autoAcceptPlan,
      workspacePath: managerState.workspacePath,
      activeFeatureIds: [featureId],
      features: [selectedFeature, ...(featureDependencies ?? [])],
      featureDependencies: featureDependencies ?? [],
      featureDependencyMap: dependencyMap,
      featureDescription: clarifyFeatureDescription(selectedFeature),
      programmerSession: managerState.programmerSession,
      messages: managerState.messages,
    };

    if (existingPlannerSession?.threadId && existingPlannerSession?.runId) {
      const runIdentifiers = {
        run_id: existingPlannerSession.runId,
        thread_id: plannerThreadId,
      } as const;

      const updatedManagerState: ManagerGraphUpdate = {
        plannerSession: {
          threadId: plannerThreadId,
          runId: existingPlannerSession.runId,
        },
        activeFeatureIds: [featureId],
        featureGraph: reconciledGraph,
      };

      await client.threads.updateState<ManagerGraphState>(threadId, {
        values: {
          ...managerState,
          ...updatedManagerState,
        },
        asNode: "start-planner",
      });

      await client.threads.patchState(threadId, {
        configurable: {
          ...(managerThreadState.metadata?.configurable ?? {}),
          ...runIdentifiers,
        },
      });

      return NextResponse.json({
        planner_thread_id: plannerThreadId,
        run_id: existingPlannerSession.runId,
      });
    }

    const plannerRunConfigurable = {
      ...getCustomConfigurableFields({
        configurable: (managerThreadState.metadata?.configurable ?? {}) as
          | GraphConfig["configurable"]
          | undefined,
      } as GraphConfig),
      ...(managerState.workspacePath
        ? { workspacePath: managerState.workspacePath }
        : {}),
      ...(process.env.OPEN_SWE_LOCAL_MODE === "true"
        ? { [LOCAL_MODE_HEADER]: "true" }
        : {}),
      thread_id: plannerThreadId,
    } satisfies Record<string, unknown>;

    const run = await client.runs
      .create(plannerThreadId, PLANNER_GRAPH_ID, {
        input: plannerRunInput,
        config: {
          recursion_limit: 400,
          configurable: plannerRunConfigurable,
        },
        ifNotExists: "create",
        streamResumable: true,
        streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
      })
      .catch((error) => {
        const message =
          error instanceof Error && error.message
            ? error.message
            : "Failed to start planner run";
        throw new Error(`Failed to start planner run: ${message}`);
      });

    const runIdentifiers = {
      run_id: run.run_id,
      thread_id: plannerThreadId,
    } as const;

    const updatedManagerState: ManagerGraphUpdate = {
      plannerSession: {
        threadId: plannerThreadId,
        runId: run.run_id,
      },
      activeFeatureIds: [featureId],
      featureGraph: reconciledGraph,
    };

    await client.threads.updateState<ManagerGraphState>(threadId, {
      values: {
        ...managerState,
        ...updatedManagerState,
      },
      asNode: "start-planner",
    });

    await client.threads.patchState(threadId, {
      configurable: {
        ...(managerThreadState.metadata?.configurable ?? {}),
        ...runIdentifiers,
      },
    });

    return NextResponse.json({
      planner_thread_id: plannerThreadId,
      run_id: run.run_id,
    });
  } catch (error) {
    if (error instanceof ApiConfigError) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    if (error instanceof ServiceUnavailableError) {
      return NextResponse.json({ error: error.message }, { status: 503 });
    }

    const message =
      error instanceof Error ? error.message : "Failed to start feature run";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
