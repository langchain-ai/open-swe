/* eslint-disable no-console */
import "dotenv/config";
import { Client } from "@langchain/langgraph-sdk";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
/* eslint-disable no-console */
interface TraceUrls {
  managerTraceUrl: string;
  plannerTraceUrl: string;
  programmerTraceUrl: string;
}

/**
 * Get the trace URLs for a given manager thread ID.
 * @param managerThreadId The ID of the manager thread.
 * @returns The trace URLs for the manager, planner, and programmer.
 */
async function getTraceUrls(managerThreadId: string): Promise<TraceUrls> {
  const {
    LANGGRAPH_API_URL: apiUrl,
    LANGCHAIN_API_KEY: apiKey,
    LANGSMITH_WORKSPACE_ID: orgId,
    LANGSMITH_PROJECT_ID: projectId,
  } = process.env;

  const missing = [apiUrl, apiKey, orgId, projectId]
    .map((val, i) =>
      !val
        ? [
            "LANGGRAPH_API_URL",
            "LANGCHAIN_API_KEY",
            "LANGSMITH_WORKSPACE_ID",
            "LANGSMITH_PROJECT_ID",
          ][i]
        : null,
    )
    .filter(Boolean);

  if (missing.length) {
    throw new Error(
      `Missing required environment variables: ${missing.join(", ")}`,
    );
  }

  const client = new Client({
    apiUrl: apiUrl!,
    apiKey: apiKey!,
    defaultHeaders: {
      "x-auth-scheme": "langsmith",
    },
  });
  const constructUrl = (runId: string) =>
    `https://smith.langchain.com/o/${orgId}/projects/p/${projectId}/r/${runId}`;

  const [managerRuns, managerState] = await Promise.all([
    client.runs.list(managerThreadId),
    client.threads.getState<ManagerGraphState>(managerThreadId),
  ]);

  const managerRunId = managerRuns?.[0]?.run_id;
  if (!managerRunId) {
    throw new Error("Unable to find run ID for manager thread.");
  }

  const result: TraceUrls = {
    managerTraceUrl: constructUrl(managerRunId),
    plannerTraceUrl: "",
    programmerTraceUrl: "",
  };

  const plannerSession = managerState.values.plannerSession;
  if (!plannerSession?.runId || !plannerSession?.threadId) {
    return result;
  }

  result.plannerTraceUrl = constructUrl(plannerSession.runId);

  const plannerState = await client.threads.getState<PlannerGraphState>(
    plannerSession.threadId,
  );
  const programmerSession = plannerState.values.programmerSession;

  if (programmerSession?.runId && programmerSession?.threadId) {
    result.programmerTraceUrl = constructUrl(programmerSession.runId);
  }

  return result;
}

// Make script executable
if (import.meta.url === `file://${process.argv[1]}`) {
  const managerThreadId = process.argv[2];

  if (!managerThreadId) {
    console.error("Usage: yarn get-trace-urls <manager-thread-id>");
    process.exit(1);
  }

  getTraceUrls(managerThreadId)
    .then((urls) => {
      console.log("\n🔗 Trace URLs:");
      console.log(`Manager:    ${urls.managerTraceUrl}`);
      console.log(`Planner:    ${urls.plannerTraceUrl || "Not available"}`);
      console.log(`Programmer: ${urls.programmerTraceUrl || "Not available"}`);
    })
    .catch((error) => {
      console.error("❌ Error:", error.message);
      process.exit(1);
    });
}
