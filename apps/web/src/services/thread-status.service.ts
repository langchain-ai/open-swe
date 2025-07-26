import { Client, Thread } from "@langchain/langgraph-sdk";
import { createClient } from "@/providers/client";
import {
  ThreadUIStatus,
  ThreadStatusData,
  mapLangGraphToUIStatus,
} from "@/lib/schemas/thread-status";
import { GraphState, TaskPlan } from "@open-swe/shared/open-swe/types";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { getActivePlanItems } from "@open-swe/shared/open-swe/tasks";
import { SessionCache, SessionCacheData } from "@/hooks/useThreadsStatus";

function getErrorFields(error: unknown): {
  message: string;
  type: "not_found" | "unauthorized";
} {
  if (
    !error ||
    typeof error !== "object" ||
    !("message" in error) ||
    !("status" in error)
  ) {
    return {
      message: "Unknown error",
      type: "unauthorized",
    };
  }

  if (error.status === 404) {
    return {
      message: "Thread not found",
      type: "not_found",
    };
  }

  if (error.status === 401) {
    return {
      message: "Unauthorized",
      type: "unauthorized",
    };
  }

  return {
    message: "Unknown error",
    type: "unauthorized",
  };
}

interface StatusResult {
  graph: "manager" | "planner" | "programmer";
  runId: string;
  threadId: string;
  status: ThreadUIStatus;
  taskPlan?: TaskPlan;
}

/**
 * Determines if all tasks in a task plan are completed
 */
function areAllPlanItemsCompleted(taskPlan: TaskPlan): boolean {
  if (!taskPlan?.tasks || !Array.isArray(taskPlan.tasks)) {
    return false;
  }
  const activePlanItems = getActivePlanItems(taskPlan);
  return activePlanItems.every((planItem) => planItem.completed);
}

export class StatusResolver {
  resolve(
    manager: StatusResult,
    planner?: StatusResult,
    programmer?: StatusResult,
  ): ThreadStatusData {
    if (manager.status === "running" || manager.status === "error") {
      return manager;
    }

    if (!planner) {
      return manager;
    }

    if (planner.status === "running" || planner.status === "paused") {
      return planner;
    }

    if (planner.status === "error") {
      return planner;
    }

    if (!programmer) {
      return planner;
    }

    return programmer;
  }
}

const CACHE_TTL = 30 * 1000;

function getCachedSessionData(
  sessionCache: SessionCache | undefined,
  sessionKey: string,
): SessionCacheData | null {
  if (!sessionCache) return null;

  const cached = sessionCache.get(sessionKey);
  if (!cached) return null;

  const isExpired = Date.now() - cached.timestamp > CACHE_TTL;
  if (isExpired) {
    sessionCache.delete(sessionKey);
    return null;
  }

  return cached;
}

function setCachedSessionData(
  sessionCache: SessionCache | undefined,
  sessionKey: string,
  data: Partial<SessionCacheData>,
): void {
  if (!sessionCache) return;

  sessionCache.set(sessionKey, {
    ...data,
    timestamp: Date.now(),
  });
}

export async function fetchThreadStatus(
  threadId: string,
  lastPollingState: ThreadStatusData | null = null,
  managerThreadData?: Thread<ManagerGraphState> | null,
  sessionCache?: SessionCache,
): Promise<ThreadStatusData> {
  try {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
    if (!apiUrl) {
      throw new Error("API URL not configured");
    }

    const client = createClient(apiUrl);
    const resolver = new StatusResolver();

    if (lastPollingState) {
      try {
        const optimizedResult = await checkLastKnownGraph(
          client,
          lastPollingState,
          resolver,
        );
        if (optimizedResult) {
          return optimizedResult;
        }
      } catch (error) {
        console.warn(
          "Optimization check failed, falling back to full status check:",
          error,
        );
      }
    }

    return await performFullStatusCheck(
      client,
      threadId,
      resolver,
      managerThreadData,
      sessionCache,
    );
  } catch (error) {
    const errorFields = getErrorFields(error);
    console.error(`Error fetching thread status for ${threadId}:`, error);

    const graph = lastPollingState?.graph || "manager";
    const runId = lastPollingState?.runId || "";
    const errorThreadId = lastPollingState?.threadId || threadId;

    return {
      graph,
      runId,
      threadId: errorThreadId,
      status: "error",
      error: errorFields,
    };
  }
}

async function checkLastKnownGraph(
  client: Client,
  lastState: ThreadStatusData,
  resolver: StatusResolver,
): Promise<ThreadStatusData | null> {
  switch (lastState.graph) {
    case "programmer":
      if (lastState.threadId && lastState.runId) {
        const programmerThread = await client.threads.get<GraphState>(
          lastState.threadId,
        );

        // Use thread status directly for most cases
        let programmerStatusValue = mapLangGraphToUIStatus(
          programmerThread.status,
        );

        // Check task completion when thread is idle - no run status needed
        if (
          programmerThread.status === "idle" &&
          areAllPlanItemsCompleted(programmerThread.values?.taskPlan)
        ) {
          programmerStatusValue = "completed";
        }

        const programmerStatus: StatusResult = {
          graph: "programmer",
          runId: lastState.runId,
          threadId: lastState.threadId,
          status: programmerStatusValue,
          taskPlan: programmerThread.values?.taskPlan,
        };

        if (
          programmerStatus.status === "running" ||
          programmerStatus.status === "error"
        ) {
          return programmerStatus;
        }

        return null;
      }
      break;

    case "planner":
      if (lastState.threadId && lastState.runId) {
        const plannerThread = await client.threads.get<PlannerGraphState>(
          lastState.threadId,
        );

        // Use thread status directly for most cases
        let plannerStatusValue = mapLangGraphToUIStatus(plannerThread.status);

        // Special case: check for interrupts even if thread status doesn't show interrupted
        if (
          plannerThread.interrupts &&
          Array.isArray(plannerThread.interrupts) &&
          plannerThread.interrupts.length > 0
        ) {
          plannerStatusValue = "paused";
        }

        // No need to check run status for planners - thread status is sufficient

        const plannerStatus: StatusResult = {
          graph: "planner",
          runId: lastState.runId,
          threadId: lastState.threadId,
          status: plannerStatusValue,
        };

        if (
          plannerStatus.status === "running" ||
          plannerStatus.status === "paused" ||
          plannerStatus.status === "error"
        ) {
          return plannerStatus;
        }

        if (plannerThread.values?.programmerSession) {
          const programmerSession = plannerThread.values.programmerSession;
          const programmerThread = await client.threads.get<GraphState>(
            programmerSession.threadId,
          );

          // Use thread status directly for most cases
          let programmerStatusValue = mapLangGraphToUIStatus(
            programmerThread.status,
          );

          // Check task completion when thread is idle - no run status needed
          if (
            programmerThread.status === "idle" &&
            areAllPlanItemsCompleted(programmerThread.values?.taskPlan)
          ) {
            programmerStatusValue = "completed";
          }

          const programmerStatus: StatusResult = {
            graph: "programmer",
            runId: programmerSession.runId,
            threadId: programmerSession.threadId,
            status: programmerStatusValue,
            taskPlan: programmerThread.values?.taskPlan,
          };

          return resolver.resolve(
            {
              graph: "manager",
              runId: "",
              threadId: lastState.threadId,
              status: "idle",
            },
            plannerStatus,
            programmerStatus,
          );
        }

        return plannerStatus;
      }
      break;

    case "manager": {
      const managerThread = await client.threads.get<ManagerGraphState>(
        lastState.threadId,
      );
      const managerStatus: StatusResult = {
        graph: "manager",
        runId: "",
        threadId: lastState.threadId,
        status: mapLangGraphToUIStatus(managerThread.status),
      };

      if (
        managerStatus.status === "running" ||
        managerStatus.status === "error"
      ) {
        return managerStatus;
      }

      return null;
    }
  }

  return null;
}

async function performFullStatusCheck(
  client: Client,
  threadId: string,
  resolver: StatusResolver,
  managerThreadData?: Thread<ManagerGraphState> | null,
  sessionCache?: SessionCache,
): Promise<ThreadStatusData> {
  let managerThread: Thread<ManagerGraphState>;

  if (managerThreadData) {
    managerThread = managerThreadData;
  } else {
    managerThread = await client.threads.get<ManagerGraphState>(threadId);
  }

  const managerStatus: StatusResult = {
    graph: "manager",
    runId: "",
    threadId,
    status: mapLangGraphToUIStatus(managerThread.status),
  };

  // If manager is running or has error, return immediately without checking sub-sessions
  if (managerStatus.status === "running" || managerStatus.status === "error") {
    return resolver.resolve(managerStatus);
  }

  if (!managerThread.values?.plannerSession) {
    return resolver.resolve(managerStatus);
  }

  const plannerSession = managerThread.values.plannerSession;
  const plannerCacheKey = `planner:${plannerSession.threadId}:${plannerSession.runId}`;

  let plannerThread: Thread<PlannerGraphState>;
  const cachedPlannerData = getCachedSessionData(sessionCache, plannerCacheKey);

  if (cachedPlannerData?.plannerData) {
    plannerThread = cachedPlannerData.plannerData.thread;
  } else {
    plannerThread = await client.threads.get<PlannerGraphState>(
      plannerSession.threadId,
    );

    // No run fetch needed for planners - thread status is sufficient

    setCachedSessionData(sessionCache, plannerCacheKey, {
      plannerData: { thread: plannerThread },
    });
  }

  // Use thread status directly for most cases
  let plannerStatusValue = mapLangGraphToUIStatus(plannerThread.status);

  // Special case: check for interrupts even if thread status doesn't show interrupted
  if (
    plannerThread.interrupts &&
    Array.isArray(plannerThread.interrupts) &&
    plannerThread.interrupts.length > 0
  ) {
    plannerStatusValue = "paused";
  }

  // No run status check needed for planners

  const plannerStatus: StatusResult = {
    graph: "planner",
    runId: plannerSession.runId,
    threadId: plannerSession.threadId,
    status: plannerStatusValue,
  };

  if (
    plannerStatus.status === "running" ||
    plannerStatus.status === "paused" ||
    plannerStatus.status === "error"
  ) {
    return resolver.resolve(managerStatus, plannerStatus);
  }

  if (!plannerThread.values?.programmerSession) {
    return resolver.resolve(managerStatus, plannerStatus);
  }

  const programmerSession = plannerThread.values.programmerSession;
  const programmerCacheKey = `programmer:${programmerSession.threadId}:${programmerSession.runId}`;

  let programmerThread: Thread<GraphState>;
  const cachedProgrammerData = getCachedSessionData(
    sessionCache,
    programmerCacheKey,
  );

  if (cachedProgrammerData?.programmerData) {
    programmerThread = cachedProgrammerData.programmerData.thread;
  } else {
    programmerThread = await client.threads.get<GraphState>(
      programmerSession.threadId,
    );

    // No run fetch needed - we only check task completion from thread data

    setCachedSessionData(sessionCache, programmerCacheKey, {
      programmerData: { thread: programmerThread },
    });
  }

  // Use thread status directly for most cases
  let programmerStatusValue = mapLangGraphToUIStatus(programmerThread.status);

  // Check task completion when thread is idle - no run status needed
  if (
    programmerThread.status === "idle" &&
    areAllPlanItemsCompleted(programmerThread.values?.taskPlan)
  ) {
    programmerStatusValue = "completed";
  }

  const programmerStatus: StatusResult = {
    graph: "programmer",
    runId: programmerSession.runId,
    threadId: programmerSession.threadId,
    status: programmerStatusValue,
    taskPlan: programmerThread.values?.taskPlan,
  };

  return resolver.resolve(managerStatus, plannerStatus, programmerStatus);
}
