import { randomUUID } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import { Client, StreamMode } from "@langchain/langgraph-sdk";
import {
  LOCAL_MODE_HEADER,
  OPEN_SWE_STREAM_MODE,
  DESIGN_GRAPH_ID,
  MANAGER_GRAPH_ID,
} from "@openswe/shared/constants";
import type { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";
import type { DesignGraphUpdate } from "@openswe/shared/open-swe/design/types";
import { coerceFeatureGraph } from "@/lib/coerce-feature-graph";

function resolveApiUrl(): string {
  return (
    process.env.LANGGRAPH_API_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://localhost:2024"
  );
}

/**
 * POST /api/design/create
 *
 * Creates a new isolated design thread for feature graph design conversations.
 * This ensures design work doesn't conflict with manager or planner threads.
 *
 * Body:
 * - manager_thread_id?: string - Optional reference to parent manager thread
 * - initial_prompt?: string - Optional initial message to start the design conversation
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  try {
    const body = await request.json();
    const managerThreadId = typeof body?.manager_thread_id === "string"
      ? body.manager_thread_id.trim()
      : typeof body?.managerThreadId === "string"
        ? body.managerThreadId.trim()
        : undefined;
    const initialPrompt = typeof body?.initial_prompt === "string"
      ? body.initial_prompt.trim()
      : typeof body?.initialPrompt === "string"
        ? body.initialPrompt.trim()
        : undefined;

    const client = new Client({
      apiUrl: resolveApiUrl(),
      defaultHeaders:
        process.env.OPEN_SWE_LOCAL_MODE === "true"
          ? { [LOCAL_MODE_HEADER]: "true" }
          : undefined,
    });

    // Generate a new, isolated design thread ID
    const designThreadId = randomUUID();

    // Prepare the initial input for the design thread
    let designInput: DesignGraphUpdate = {};

    // If a manager thread is provided, inherit workspace and repository context
    if (managerThreadId) {
      try {
        const managerThreadState =
          await client.threads.getState<ManagerGraphState>(managerThreadId);

        if (managerThreadState?.values) {
          const managerState = managerThreadState.values;
          const featureGraph = coerceFeatureGraph(managerState.featureGraph);

          designInput = {
            targetRepository: managerState.targetRepository,
            workspacePath: managerState.workspacePath,
            managerThreadId,
            featureGraph: featureGraph ?? undefined,
          };
        }
      } catch (error) {
        // Manager thread not found or inaccessible - proceed without context
        console.warn("Could not load manager thread state:", error);
      }
    }

    // Add initial message if provided
    if (initialPrompt) {
      designInput.messages = [
        {
          type: "human",
          content: initialPrompt,
        },
      ] as any; // Type coercion needed for message format
    }

    // Create the design thread with an initial run
    const run = await client.runs.create(designThreadId, DESIGN_GRAPH_ID, {
      input: designInput,
      config: {
        recursion_limit: 200,
        configurable: {
          ...(process.env.OPEN_SWE_LOCAL_MODE === "true"
            ? { [LOCAL_MODE_HEADER]: "true" }
            : {}),
        },
      },
      ifNotExists: "create",
      streamResumable: true,
      streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
    });

    return NextResponse.json({
      design_thread_id: designThreadId,
      run_id: run.run_id,
      manager_thread_id: managerThreadId ?? null,
      status: "created",
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to create design thread";
    console.error("Design thread creation failed:", error);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
