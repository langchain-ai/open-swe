import { Client } from "@langchain/langgraph-sdk";
import { MANAGER_GRAPH_ID } from "@openswe/shared/constants";
import type { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";

import { createClient } from "@/providers/client";
import {
  FeatureGraphFetchResult,
  mapFeatureGraphPayload,
  mapFeatureProposalState,
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
  const { proposals, activeProposalId } = mapFeatureProposalState(
    thread?.values?.featureProposals,
  );

  return {
    graph,
    activeFeatureIds,
    proposals,
    activeProposalId,
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

export type FeatureProposalAction = "approve" | "reject" | "info";

export interface FeatureProposalActionResponse extends FeatureGraphFetchResult {
  message: string | null;
}

export async function performFeatureProposalAction({
  threadId,
  proposalId,
  featureId,
  action,
  rationale,
}: {
  threadId: string;
  proposalId: string;
  featureId: string;
  action: FeatureProposalAction;
  rationale?: string;
}): Promise<FeatureProposalActionResponse> {
  const response = await fetch("/api/feature-graph/proposal", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      thread_id: threadId,
      proposal_id: proposalId,
      feature_id: featureId,
      action,
      rationale,
    }),
  });

  const payload: unknown = await response.json().catch(() => null);

  if (!response.ok) {
    const message =
      (payload && typeof (payload as { error?: unknown }).error === "string"
        ? (payload as { error: string }).error
        : null) ?? "Failed to process proposal action";
    throw new Error(message);
  }

  const result = mapFeatureGraphPayload(payload);

  return {
    ...result,
    message: result.message ?? "Proposal updated",
  } satisfies FeatureProposalActionResponse;
}

function getApiUrl(): string {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
  if (!apiUrl) {
    throw new Error("API URL not configured");
  }
  return apiUrl;
}
