import {
  PROGRAMMER_GRAPH_ID,
  PLANNER_GRAPH_ID,
  MANAGER_GRAPH_ID,
  LOCAL_MODE_HEADER,
} from "@openswe/shared/constants";
import { NextRequest, NextResponse } from "next/server";
import { RestartRunRequest } from "./types";
import { Client } from "@langchain/langgraph-sdk";
import { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@openswe/shared/open-swe/planner/types";
import { GraphState } from "@openswe/shared/open-swe/types";
import { createNewSession } from "./create-new-session";

/**
 * Restart a run. This function isn't actually restarting a run,
 * but rather it's creating fresh new threads & runs for all existing
 * threads. Whichever thread was the one to fail will be restarted and
 * resumed where it was failed.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  try {
    const body: RestartRunRequest = await request.json();
    const { managerThreadId, plannerThreadId, programmerThreadId } = body;

    const langGraphClient = new Client({
      apiUrl: process.env.LANGGRAPH_API_URL ?? "http://localhost:2024",
      defaultHeaders:
        process.env.OPEN_SWE_LOCAL_MODE === "true"
          ? { [LOCAL_MODE_HEADER]: "true" }
          : undefined,
    });

    const [
      managerThread,
      managerThreadState,
      plannerThread,
      plannerThreadState,
      programmerThread,
      programmerThreadState,
    ] = await Promise.all([
      langGraphClient.threads.get<ManagerGraphState>(managerThreadId),
      langGraphClient.threads.getState<ManagerGraphState>(managerThreadId),
      langGraphClient.threads.get<PlannerGraphState>(plannerThreadId),
      langGraphClient.threads.getState<PlannerGraphState>(plannerThreadId),
      programmerThreadId
        ? langGraphClient.threads.get<GraphState>(programmerThreadId)
        : null,
      programmerThreadId
        ? langGraphClient.threads.getState<GraphState>(programmerThreadId)
        : null,
    ]);
    if (!managerThreadState || !plannerThreadState) {
      return NextResponse.json(
        {
          error:
            "Failed to restart run. Must have existing planner and manager threads.",
        },
        { status: 500 },
      );
    }

    const newProgrammerSession = programmerThreadState
      ? await createNewSession(langGraphClient, {
          graphId: PROGRAMMER_GRAPH_ID,
          threadState: programmerThreadState,
          threadConfig: (programmerThread as Record<string, any>)?.config,
        })
      : undefined;

    const newPlannerState: PlannerGraphState = {
      ...plannerThreadState.values,
      ...(newProgrammerSession
        ? {
            programmerSession: newProgrammerSession,
          }
        : {}),
    };
    const newPlannerSession = await createNewSession(langGraphClient, {
      graphId: PLANNER_GRAPH_ID,
      threadState: {
        ...plannerThreadState,
        values: newPlannerState,
      },
      threadConfig: (plannerThread as Record<string, any>)?.config,
    });

    const newManagerState: ManagerGraphState = {
      ...managerThreadState.values,
      plannerSession: newPlannerSession,
    };
    const newManagerSession = await createNewSession(langGraphClient, {
      graphId: MANAGER_GRAPH_ID,
      threadState: {
        ...managerThreadState,
        values: newManagerState,
      },
      threadConfig: (managerThread as Record<string, any>)?.config,
    });

    return NextResponse.json({
      managerSession: newManagerSession,
      plannerSession: newPlannerSession,
      programmerSession: newProgrammerSession,
    });
  } catch (error) {
    console.error("Failed to restart run", error);
    return NextResponse.json(
      { error: "Failed to restart run" },
      { status: 500 },
    );
  }
}
