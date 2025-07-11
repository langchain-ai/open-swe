import { Thread, Run } from "@langchain/langgraph-sdk";
import { createClient } from "@/providers/client";
import {
  ThreadUIStatus,
  ThreadStatusData,
  mapLangGraphToUIStatus,
} from "@/lib/schemas/thread-status";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";

interface StatusResult {
  graph: "manager" | "planner" | "programmer";
  runId: string;
  threadId: string;
  status: ThreadUIStatus;
}

/**
 * Determines if all tasks in a task plan are completed
 */
function areAllTasksCompleted(taskPlan: any): boolean {
  if (!taskPlan?.tasks || !Array.isArray(taskPlan.tasks)) {
    return false;
  }
  return taskPlan.tasks.every((task: any) => task.completed === true);
}

/**
 * Maps LangGraph SDK status to display status
 */
function mapLangGraphStatusToDisplay(status: string): ThreadUIStatus {
  switch (status) {
    case "busy":
      return "running";
    case "idle":
      return "idle";
    case "error":
      return "error";
    case "interrupted":
      return "paused";
    default:
      return "idle";
  }
}

/**
 * Fetches manager thread status
 */
async function getManagerStatus(
  client: ReturnType<typeof createClient>,
  threadId: string,
): Promise<StatusResult> {
  const managerThread = await client.threads.get<ManagerGraphState>(threadId);
  const status = mapLangGraphStatusToDisplay(managerThread.status);

  return {
    graph: "manager",
    runId: "",
    threadId,
    status,
  };
}

/**
 * Fetches planner run and thread status
 */
async function getPlannerStatus(
  client: ReturnType<typeof createClient>,
  plannerSession: {
    threadId: string;
    runId: string;
  },
): Promise<StatusResult> {
  const [plannerRun, plannerThread] = await Promise.all([
    client.runs.get(plannerSession.threadId, plannerSession.runId),
    client.threads.get<PlannerGraphState>(plannerSession.threadId),
  ]);

  let status: ThreadUIStatus;

  // First check if the thread itself is interrupted (plan waiting for approval)
  if (plannerThread.status === "interrupted") {
    status = "paused";
  }
  // Then check if the thread has interrupts (plan waiting for approval)
  else if (
    plannerThread.interrupts &&
    Array.isArray(plannerThread.interrupts) &&
    plannerThread.interrupts.length > 0
  ) {
    status = "paused";
  } else if (plannerRun.status === "running") {
    status = "running";
  } else if (plannerRun.status === "interrupted") {
    status = "paused";
  } else if (plannerRun.status === "timeout") {
    status = "error";
  } else if (
    plannerRun.status === "success" &&
    !plannerThread.values?.programmerSession
  ) {
    status = "idle";
  } else {
    status = "idle";
  }

  return {
    graph: "planner",
    runId: plannerSession.runId,
    threadId: plannerSession.threadId,
    status,
  };
}

/**
 * Fetches programmer run and thread status
 */
async function getProgrammerStatus(
  client: ReturnType<typeof createClient>,
  programmerSession: {
    threadId: string;
    runId: string;
  },
): Promise<StatusResult> {
  const [programmerRun, programmerThread] = await Promise.all([
    client.runs.get(programmerSession.threadId, programmerSession.runId),
    client.threads.get<GraphState>(programmerSession.threadId),
  ]);

  let status: ThreadUIStatus;
  if (programmerRun.status === "running") {
    status = "running";
  } else if (programmerRun.status === "timeout") {
    status = "error";
  } else if (
    programmerRun.status === "success" &&
    areAllTasksCompleted(programmerThread.values?.taskPlan)
  ) {
    status = "completed";
  } else {
    status = "idle";
  }

  return {
    graph: "programmer",
    runId: programmerSession.runId,
    threadId: programmerSession.threadId,
    status,
  };
}

/**
 * Status resolver using priority logic:
 * manager running > planner running > planner interrupted (paused) > programmer running > completed/idle/error
 */
export class StatusResolver {
  resolve(
    manager: StatusResult,
    planner?: StatusResult,
    programmer?: StatusResult,
  ): ThreadStatusData {
    // Priority 1: Manager is running or has error
    if (manager.status === "running" || manager.status === "error") {
      const result = manager;
      return result;
    }

    // Priority 2: No planner session - manager is idle
    if (!planner) {
      const result = manager;
      return result;
    }

    // Priority 3: Planner is running or paused (interrupted)
    if (planner.status === "running" || planner.status === "paused") {
      const result = planner;
      return result;
    }

    // Priority 4: Planner has error
    if (planner.status === "error") {
      const result = planner;
      return result;
    }

    // Priority 5: No programmer session - planner is idle
    if (!programmer) {
      const result = planner;
      return result;
    }

    // Priority 6: Return programmer status (running, completed, idle, or error)
    const result = programmer;
    return result;
  }
}

/**
 * Main service function to fetch thread status
 */
export async function fetchThreadStatus(
  threadId: string,
  lastPollingState: ThreadStatusData | null = null,
): Promise<ThreadStatusData> {
  try {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
    if (!apiUrl) {
      console.error("[fetchThreadStatus] Missing API URL");
      throw new Error("API URL not configured");
    }

    const client = createClient(apiUrl);
    const resolver = new StatusResolver();

    // Step 1: Get manager status
    const managerStatus = await getManagerStatus(client, threadId);

    const managerThread = await client.threads.get<ManagerGraphState>(threadId);

    // Early returns for manager-level conditions
    if (
      managerStatus.status === "running" ||
      managerStatus.status === "error"
    ) {
      const result = resolver.resolve(managerStatus);
      return result;
    }

    if (!managerThread.values?.plannerSession) {
      const result = resolver.resolve(managerStatus);
      return result;
    }

    // Step 2: Get planner status
    const plannerStatus = await getPlannerStatus(
      client,
      managerThread.values.plannerSession,
    );

    const plannerThread = await client.threads.get<PlannerGraphState>(
      managerThread.values.plannerSession.threadId,
    );

    // Early returns for planner-level conditions
    if (
      plannerStatus.status === "running" ||
      plannerStatus.status === "paused" ||
      plannerStatus.status === "error"
    ) {
      const result = resolver.resolve(managerStatus, plannerStatus);
      return result;
    }

    if (!plannerThread.values?.programmerSession) {
      const result = resolver.resolve(managerStatus, plannerStatus);
      return result;
    }

    // Step 3: Get programmer status
    const programmerStatus = await getProgrammerStatus(
      client,
      plannerThread.values.programmerSession,
    );

    // Return final resolved status
    const result = resolver.resolve(
      managerStatus,
      plannerStatus,
      programmerStatus,
    );
    return result;
  } catch (error) {
    console.error(`[fetchThreadStatus] Error for thread ${threadId}:`, error);

    // Return error status with fallback information
    const graph = lastPollingState?.graph || "manager";
    const runId = lastPollingState?.runId || "";
    const errorThreadId = lastPollingState?.threadId || threadId;

    return {
      graph,
      runId,
      threadId: errorThreadId,
      status: "error",
    };
  }
}
