import { randomUUID } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import { Client, StreamMode } from "@langchain/langgraph-sdk";
import {
  LOCAL_MODE_HEADER,
  OPEN_SWE_STREAM_MODE,
  PLANNER_GRAPH_ID,
} from "@openswe/shared/constants";
import type {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import type { PlannerGraphUpdate } from "@openswe/shared/open-swe/planner/types";
import type { GraphConfig } from "@openswe/shared/open-swe/types";
import { getCustomConfigurableFields } from "@openswe/shared/open-swe/utils/config";
import { coerceFeatureGraph } from "@/lib/coerce-feature-graph";

function resolveApiUrl(): string {
  return (
    process.env.LANGGRAPH_API_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://localhost:2024"
  );
}

function resolveThreadId(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  return null;
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

    const managerThreadState =
      await client.threads.getState<ManagerGraphState>(threadId);

    if (!managerThreadState?.values) {
      return NextResponse.json(
        { error: "Manager state not found for thread" },
        { status: 404 },
      );
    }

    const managerState = managerThreadState.values;
    const featureGraph = coerceFeatureGraph(managerState.featureGraph);
    const plannerThreadId =
      managerState.plannerSession?.threadId ?? randomUUID();

    const selectedFeature = featureGraph?.getFeature(featureId);

    if (!selectedFeature) {
      return NextResponse.json(
        { error: "Feature not found in manager state" },
        { status: 404 },
      );
    }

    const featureDependencies = featureGraph
      ?.getNeighbors(featureId, "both")
      .filter((neighbor) => neighbor.id !== selectedFeature.id);

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
      programmerSession: managerState.programmerSession,
      messages: managerState.messages,
    };

    const run = await client.runs.create(plannerThreadId, PLANNER_GRAPH_ID, {
      input: plannerRunInput,
      config: {
        recursion_limit: 400,
        configurable: {
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
        },
      },
      ifNotExists: "create",
      streamResumable: true,
      streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
    });

    const updatedManagerState: ManagerGraphUpdate = {
      plannerSession: {
        threadId: plannerThreadId,
        runId: run.run_id,
      },
      activeFeatureIds: [featureId],
    };

    await client.threads.updateState<ManagerGraphState>(threadId, {
      values: {
        ...managerState,
        ...updatedManagerState,
      },
      asNode: "start-planner",
    });

    return NextResponse.json({
      planner_thread_id: plannerThreadId,
      run_id: run.run_id,
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to start feature run";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
