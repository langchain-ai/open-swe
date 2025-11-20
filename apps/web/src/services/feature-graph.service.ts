import { Client } from "@langchain/langgraph-sdk";
import { MANAGER_GRAPH_ID } from "@openswe/shared/constants";
import type { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";

import { createClient } from "@/providers/client";
import {
  FeatureGraphFetchResult,
  normalizeFeatureIds,
} from "@/lib/feature-graph-payload";
import { coerceFeatureGraph } from "@/lib/coerce-feature-graph";

export interface FeatureDevelopmentResponse {
  plannerThreadId: string;
  runId: string;
}

export async function fetchFeatureGraph(
  threadId: string,
  client?: Client<ManagerGraphState>,
): Promise<FeatureGraphFetchResult> {
  if (!threadId) {
    throw new Error("Thread id is required to fetch feature graph data");
  }

  const resolvedClient = client ?? createClient(getApiUrl());

  const thread = await resolvedClient.threads.get<ManagerGraphState>(threadId);
  const graph = coerceFeatureGraph(thread?.values?.featureGraph);
  const activeFeatureIds = normalizeFeatureIds(
    thread?.values?.activeFeatureIds,
  );

  return {
    graph,
    activeFeatureIds,
  };
}

export async function requestFeatureGraphGeneration(
  threadId: string,
  client?: Client<ManagerGraphState>,
): Promise<void> {
  if (!threadId) {
    throw new Error(
      "Thread id is required to request feature graph generation",
    );
  }

  const resolvedClient = client ?? createClient(getApiUrl());

  await resolvedClient.runs.create(threadId, MANAGER_GRAPH_ID, {
    input: {
      action: "generate_feature_graph",
    },
    ifNotExists: "create",
  });
}

export async function startFeatureDevelopmentRun(
  threadId: string,
  featureId: string,
): Promise<FeatureDevelopmentResponse> {
  if (!threadId) {
    throw new Error("Thread id is required to start feature development");
  }

  if (!featureId) {
    throw new Error("Feature id is required to start feature development");
  }

  const response = await fetch("/api/feature-graph/develop", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      thread_id: threadId,
      feature_id: featureId,
    }),
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const message =
      (payload && typeof payload.error === "string" ? payload.error : null) ??
      "Failed to start feature development";
    throw new Error(message);
  }

  const payload = await response.json();
  const { planner_thread_id: plannerThreadId, run_id: runId } = payload ?? {};

  if (typeof plannerThreadId !== "string" || typeof runId !== "string") {
    throw new Error("Invalid response when starting feature development");
  }

  return { plannerThreadId, runId } satisfies FeatureDevelopmentResponse;
}

function getApiUrl(): string {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
  if (!apiUrl) {
    throw new Error("API URL not configured");
  }
  return apiUrl;
}
