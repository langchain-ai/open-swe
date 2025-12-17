import { Client } from "@langchain/langgraph-sdk";
import { DESIGN_GRAPH_ID } from "@openswe/shared/constants";
import type { DesignGraphState } from "@openswe/shared/open-swe/design/types";

function getApiUrl(): string {
  return (
    process.env.NEXT_PUBLIC_API_URL ??
    process.env.LANGGRAPH_API_URL ??
    "http://localhost:2024"
  );
}

function createClient(apiUrl?: string): Client {
  return new Client({ apiUrl: apiUrl ?? getApiUrl() });
}

export interface CreateDesignThreadOptions {
  managerThreadId?: string;
  initialPrompt?: string;
}

export interface CreateDesignThreadResult {
  designThreadId: string;
  runId: string;
  managerThreadId: string | null;
}

export interface HandoffOptions {
  designThreadId: string;
  featureIds?: string[];
}

export interface HandoffResult {
  plannerThreadId: string;
  runId: string;
  featureIds: string[];
  featureCount: number;
  dependencyCount: number;
}

export interface DesignThreadState {
  threadId: string;
  managerThreadId: string | null;
  workspacePath: string | null;
  featureGraph: unknown;
  readyFeatureIds: string[];
  pendingProposals: unknown[];
  clarifyingQuestions: unknown[];
  designSession: unknown;
  changeHistory: unknown[];
  impactAnalysis: Record<string, unknown>;
}

/**
 * Creates a new design thread for feature graph design conversations.
 */
export async function createDesignThread(
  options?: CreateDesignThreadOptions,
): Promise<CreateDesignThreadResult> {
  const response = await fetch("/api/design/create", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      manager_thread_id: options?.managerThreadId,
      initial_prompt: options?.initialPrompt,
    }),
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error ?? "Failed to create design thread");
  }

  const data = await response.json();

  return {
    designThreadId: data.design_thread_id,
    runId: data.run_id,
    managerThreadId: data.manager_thread_id ?? null,
  };
}

/**
 * Fetches the current state of a design thread.
 */
export async function fetchDesignThreadState(
  threadId: string,
): Promise<DesignThreadState> {
  const response = await fetch(
    `/api/design/state?thread_id=${encodeURIComponent(threadId)}`,
  );

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error ?? "Failed to fetch design thread state");
  }

  const data = await response.json();

  return {
    threadId: data.thread_id,
    managerThreadId: data.manager_thread_id,
    workspacePath: data.workspace_path,
    featureGraph: data.feature_graph,
    readyFeatureIds: data.ready_feature_ids ?? [],
    pendingProposals: data.pending_proposals ?? [],
    clarifyingQuestions: data.clarifying_questions ?? [],
    designSession: data.design_session,
    changeHistory: data.change_history ?? [],
    impactAnalysis: data.impact_analysis ?? {},
  };
}

/**
 * Hands off features from a design thread to a new planner thread.
 */
export async function handoffToPlanner(
  options: HandoffOptions,
): Promise<HandoffResult> {
  const response = await fetch("/api/design/handoff", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      design_thread_id: options.designThreadId,
      feature_ids: options.featureIds,
    }),
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error ?? "Failed to hand off to planner");
  }

  const data = await response.json();

  return {
    plannerThreadId: data.planner_thread_id,
    runId: data.run_id,
    featureIds: data.feature_ids ?? [],
    featureCount: data.feature_count ?? 0,
    dependencyCount: data.dependency_count ?? 0,
  };
}

/**
 * Sends a message to a design thread using the LangGraph SDK directly.
 */
export async function sendDesignMessage(
  threadId: string,
  message: string,
  client?: Client,
): Promise<void> {
  const resolvedClient = client ?? createClient();

  await resolvedClient.runs.create(threadId, DESIGN_GRAPH_ID, {
    input: {
      messages: [
        {
          type: "human",
          content: message,
        },
      ],
    },
  });
}

/**
 * Gets design thread state directly from the LangGraph SDK.
 */
export async function getDesignThreadStateSDK(
  threadId: string,
  client?: Client,
): Promise<DesignGraphState | null> {
  const resolvedClient = client ?? createClient();

  try {
    const state = await resolvedClient.threads.getState<DesignGraphState>(threadId);
    return state?.values ?? null;
  } catch {
    return null;
  }
}
