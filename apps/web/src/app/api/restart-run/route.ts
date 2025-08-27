import { v4 as uuidv4 } from "uuid";
import {
  GITHUB_TOKEN_COOKIE,
  GITHUB_INSTALLATION_ID_COOKIE,
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_INSTALLATION_NAME,
  GITHUB_INSTALLATION_ID,
  PROGRAMMER_GRAPH_ID,
  PLANNER_GRAPH_ID,
  OPEN_SWE_STREAM_MODE,
  MANAGER_GRAPH_ID,
} from "@open-swe/shared/constants";
import {
  getGitHubInstallationTokenOrThrow,
  getInstallationNameFromReq,
  getGitHubAccessTokenOrThrow,
} from "../[..._path]/utils";
import { NextRequest, NextResponse } from "next/server";
import { RestartRunRequest } from "./types";
import { Client, StreamMode, ThreadState } from "@langchain/langgraph-sdk";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { AgentSession, GraphConfig, GraphState } from "@open-swe/shared/open-swe/types";
import { END } from "@langchain/langgraph/web";
import { getCustomConfigurableFields } from "@open-swe/shared/open-swe/utils/config";

async function getRequestHeaders(
  req: NextRequest,
): Promise<Record<string, string>> {
  const encryptionKey = process.env.SECRETS_ENCRYPTION_KEY;
  if (!encryptionKey) {
    throw new Error("SECRETS_ENCRYPTION_KEY environment variable is required");
  }
  const installationIdCookie = req.cookies.get(
    GITHUB_INSTALLATION_ID_COOKIE,
  )?.value;

  if (!installationIdCookie) {
    throw new Error(
      "No GitHub installation ID found. GitHub App must be installed first.",
    );
  }
  const [installationToken, installationName] = await Promise.all([
    getGitHubInstallationTokenOrThrow(installationIdCookie, encryptionKey),
    getInstallationNameFromReq(req, installationIdCookie),
  ]);

  return {
    [GITHUB_TOKEN_COOKIE]: getGitHubAccessTokenOrThrow(req, encryptionKey),
    [GITHUB_INSTALLATION_TOKEN_COOKIE]: installationToken,
    [GITHUB_INSTALLATION_NAME]: installationName,
    [GITHUB_INSTALLATION_ID]: installationIdCookie,
  };
}

async function createNewSession(
  client: Client,
  inputs: {
    graphId: string;
    threadState: ThreadState<
      ManagerGraphState | PlannerGraphState | GraphState
    >;
    threadConfig: GraphConfig;
  },
): Promise<AgentSession> {
  const newThreadId = uuidv4();
  const hasNext = inputs.threadState.next.length > 0;

  const run = await client.runs.create(newThreadId, inputs.graphId, {
    command: {
      update: inputs.threadState.values,
      ...(hasNext ? { goto: inputs.threadState.next[0] } : { goto: END }),
    },
    ifNotExists: "create",
    streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
    streamResumable: true,
    config: {
      recursion_limit: 400,
      configurable: getCustomConfigurableFields({
        ...inputs.threadState.metadata as Record<string, any>,
        ...inputs.threadConfig?.configurable as Record<string, any>,
      }),
    },
  });
  return {
    threadId: newThreadId,
    runId: run.run_id,
  };
}

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
      defaultHeaders: await getRequestHeaders(request),
    });

    const [managerThread, managerThreadState, plannerThread, plannerThreadState, programmerThread, programmerThreadState] =
      await Promise.all([
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
