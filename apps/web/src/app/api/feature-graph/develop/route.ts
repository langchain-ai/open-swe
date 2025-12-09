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
  type FeatureDependencyMap,
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

const FEATURE_GRAPH_GENERATION_TIMEOUT_MS = 30_000;
const FEATURE_GRAPH_POLL_INTERVAL_MS = 1_000;

function resolveApiUrl(): string {
  const apiUrl =
    process.env.LANGGRAPH_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "";

  if (!apiUrl) {
    throw new Error(
      "LangGraph API URL is not configured. Set LANGGRAPH_API_URL or NEXT_PUBLIC_API_URL.",
    );
  }

  try {
    return new URL(apiUrl).toString();
  } catch (error) {
    throw new Error(
      `Invalid LangGraph API URL: ${apiUrl}. ${(error as Error)?.message ?? ""}`.trim(),
    );
  }
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

async function requestFeatureGraphGeneration(
  client: Client,
  threadId: string,
): Promise<void> {
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

async function waitForFeatureGraph(
  client: Client,
  threadId: string,
  initialState: ManagerGraphState,
): Promise<{ graph: FeatureGraph; managerState: ManagerGraphState } | null> {
  let latestState = initialState;
  const deadline = Date.now() + FEATURE_GRAPH_GENERATION_TIMEOUT_MS;

  while (Date.now() < deadline) {
    const featureGraph = coerceFeatureGraph(latestState.featureGraph);
    if (featureGraph) {
      return { graph: featureGraph, managerState: latestState };
    }

    await new Promise((resolve) =>
      setTimeout(resolve, FEATURE_GRAPH_POLL_INTERVAL_MS),
    );

    const refreshedState = await client.threads.getState<ManagerGraphState>(
      threadId,
    );

    if (refreshedState?.values) {
      latestState = refreshedState.values;
    }
  }

  return null;
}

async function ensureFeatureGraph(
  client: Client,
  threadId: string,
  managerState: ManagerGraphState,
): Promise<{ graph: FeatureGraph; managerState: ManagerGraphState }> {
  const existingGraph = coerceFeatureGraph(managerState.featureGraph);

  if (existingGraph) {
    return { graph: existingGraph, managerState };
  }

  try {
    await requestFeatureGraphGeneration(client, threadId);
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Unable to trigger feature graph generation";
    throw new Error(message);
  }

  const generated = await waitForFeatureGraph(client, threadId, managerState);

  if (!generated) {
    throw new Error("Feature graph could not be generated for this thread");
  }

  return generated;
}

async function refreshManagerState(
  client: Client,
  threadId: string,
): Promise<ManagerGraphState | null> {
  const state = await client.threads.getState<ManagerGraphState>(threadId);
  return state?.values ?? null;
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
        const status = (error as { status?: number })?.status;
        const message =
          status === 404
            ? "Manager state not found for thread"
            : "LangGraph backend is unreachable";

        return NextResponse.json(
          { error: message },
          { status: status ?? 503 },
        );
      });

    if (managerThreadState instanceof NextResponse) {
      return managerThreadState;
    }

    if (!managerThreadState?.values) {
      return NextResponse.json(
        { error: "Manager state not found for thread" },
        { status: 404 },
      );
    }

    const { graph: ensuredGraph, managerState: ensuredManagerState } =
      await ensureFeatureGraph(client, threadId, managerThreadState.values);

    const { graph: reconciledGraph, dependencyMap } =
      reconcileFeatureGraph(ensuredGraph);

    const managerState = ensuredManagerState;
    const existingPlannerSession = managerState.plannerSession;
    const plannerThreadId =
      existingPlannerSession?.threadId ?? randomUUID();

    let selectedFeature = reconciledGraph.getFeature(featureId);

    if (!selectedFeature) {
      const refreshedState = await refreshManagerState(client, threadId);

      if (refreshedState) {
        const refreshedGraph = coerceFeatureGraph(refreshedState.featureGraph);

        if (refreshedGraph) {
          const { graph, dependencyMap: refreshedDependencyMap } =
            reconcileFeatureGraph(refreshedGraph);
          managerThreadState.values = refreshedState;
          selectedFeature = graph.getFeature(featureId);

          if (selectedFeature) {
            return await handlePlannerRun({
              client,
              managerThreadState,
              managerState: refreshedState,
              reconciledGraph: graph,
              dependencyMap: refreshedDependencyMap,
              plannerThreadId,
              featureId,
              selectedFeature,
              threadId,
            });
          }
        }
      }
    }

    if (!selectedFeature) {
      return NextResponse.json(
        {
          error:
            "Feature not found in the reconciled graph. Refresh the feature graph and try again.",
        },
        { status: 404 },
      );
    }

    return await handlePlannerRun({
      client,
      managerThreadState,
      managerState,
      reconciledGraph,
      dependencyMap,
      plannerThreadId,
      featureId,
      selectedFeature,
      threadId,
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to start feature development";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

async function handlePlannerRun({
  client,
  managerThreadState,
  managerState,
  reconciledGraph,
  dependencyMap,
  plannerThreadId,
  featureId,
  selectedFeature,
  threadId,
}: {
  client: Client;
  managerThreadState: Awaited<ReturnType<Client["threads"]["getState"]>>;
  managerState: ManagerGraphState;
  reconciledGraph: FeatureGraph;
  dependencyMap: FeatureDependencyMap;
  plannerThreadId: string;
  featureId: string;
  selectedFeature: FeatureNode;
  threadId: string;
}): Promise<NextResponse> {
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

  const existingPlannerSession = managerState.plannerSession;

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
        ...(managerThreadState?.metadata?.configurable ?? {}),
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
      configurable: (managerThreadState?.metadata?.configurable ?? {}) as
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

  const run = await client.runs.create(plannerThreadId, PLANNER_GRAPH_ID, {
    input: plannerRunInput,
    config: {
      recursion_limit: 400,
      configurable: plannerRunConfigurable,
    },
    ifNotExists: "create",
    streamResumable: true,
    streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
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
      ...(managerThreadState?.metadata?.configurable ?? {}),
      ...runIdentifiers,
    },
  });

  return NextResponse.json({
    planner_thread_id: plannerThreadId,
    run_id: run.run_id,
  });
}
