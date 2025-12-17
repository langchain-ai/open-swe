import { NextRequest, NextResponse } from "next/server";
import { Client } from "@langchain/langgraph-sdk";
import { LOCAL_MODE_HEADER } from "@openswe/shared/constants";
import type { DesignGraphState } from "@openswe/shared/open-swe/design/types";

function resolveApiUrl(): string {
  return (
    process.env.LANGGRAPH_API_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://localhost:2024"
  );
}

/**
 * GET /api/design/state?thread_id=xxx
 *
 * Retrieves the current state of a design thread.
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  try {
    const { searchParams } = new URL(request.url);
    const threadId = searchParams.get("thread_id") ?? searchParams.get("threadId");

    if (!threadId) {
      return NextResponse.json(
        { error: "thread_id is required" },
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

    const threadState = await client.threads.getState<DesignGraphState>(threadId);

    if (!threadState?.values) {
      return NextResponse.json(
        { error: "Design thread state not found" },
        { status: 404 },
      );
    }

    const state = threadState.values;

    // Serialize feature graph for JSON response
    const featureGraphJson = state.featureGraph
      ? (state.featureGraph as any).toJSON?.() ?? state.featureGraph
      : null;

    return NextResponse.json({
      thread_id: threadId,
      manager_thread_id: state.managerThreadId ?? null,
      workspace_path: state.workspacePath ?? null,
      feature_graph: featureGraphJson,
      ready_feature_ids: state.readyFeatureIds ?? [],
      pending_proposals: state.pendingProposals ?? [],
      clarifying_questions: state.clarifyingQuestions ?? [],
      design_session: state.designSession ?? null,
      change_history: state.changeHistory ?? [],
      impact_analysis: state.impactAnalysis ?? {},
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to get design thread state";
    console.error("Design state retrieval failed:", error);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
