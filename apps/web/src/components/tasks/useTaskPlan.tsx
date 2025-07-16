import { TaskPlan } from "@open-swe/shared/open-swe/types";
import { useEffect, useState, useMemo } from "react";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { useThreadStatus } from "@/hooks/useThreadStatus";
import { taskPlansEqual } from "@/lib/task-plan-utils";

interface ThreadStatusDataResult {
  statusData: { taskPlan?: TaskPlan } | null;
  status: string;
  isLoading: boolean;
  error: Error | null;
  mutate: () => void;
}

/**
 * Hook that determines the active task plan based on current session state.
 * Priority: programmer session > planner session > display thread.
 */
export function useActiveTaskPlan(
  displayThreadTaskPlan?: TaskPlan,
  programmerSession?: ManagerGraphState["programmerSession"],
  displayThreadId?: string,
  plannerThreadId?: string,
) {
  const [activeTaskPlan, setActiveTaskPlan] = useState<TaskPlan>();

  const { statusData: displayThreadStatusData } = useThreadStatus(
    displayThreadId || "",
    { includeTaskPlan: true },
  ) as ThreadStatusDataResult;

  const { statusData: programmerStatusData } = useThreadStatus(
    programmerSession?.threadId || "",
    { enabled: !!programmerSession?.threadId, includeTaskPlan: true },
  ) as ThreadStatusDataResult;

  const { statusData: plannerStatusData } = useThreadStatus(
    plannerThreadId || "",
    {
      enabled: !!plannerThreadId && !!programmerSession?.threadId,
      includeTaskPlan: true,
    },
  ) as ThreadStatusDataResult;

  const selectedTaskPlan = useMemo(() => {
    if (programmerSession?.threadId && programmerStatusData?.taskPlan) {
      return programmerStatusData.taskPlan;
    }
    if (programmerSession?.threadId && plannerStatusData?.taskPlan) {
      return plannerStatusData.taskPlan;
    }
    if (displayThreadStatusData?.taskPlan) {
      return displayThreadStatusData.taskPlan;
    }
    if (displayThreadTaskPlan) {
      return displayThreadTaskPlan;
    }
    return undefined;
  }, [
    programmerSession?.threadId,
    programmerStatusData?.taskPlan,
    plannerStatusData?.taskPlan,
    displayThreadStatusData?.taskPlan,
    displayThreadTaskPlan,
  ]);

  useEffect(() => {
    if (!taskPlansEqual(activeTaskPlan, selectedTaskPlan)) {
      setActiveTaskPlan(selectedTaskPlan);
    }
  }, [selectedTaskPlan, activeTaskPlan]);

  return {
    taskPlan: activeTaskPlan,
    isProgrammerActive: !!programmerSession,
  };
}
