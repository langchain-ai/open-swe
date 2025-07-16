import { TaskPlan } from "@open-swe/shared/open-swe/types";
import { useEffect, useState, useMemo } from "react";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { useThreadStatus } from "@/hooks/useThreadStatus";

// Hook that works with task plan data directly
export function useTaskPlan(taskPlan?: TaskPlan) {
  const [currentTaskPlan, setCurrentTaskPlan] = useState<TaskPlan>();

  useEffect(() => {
    const currentPlanStr = JSON.stringify(currentTaskPlan, null, 2);
    const newPlanStr = JSON.stringify(taskPlan, null, 2);
    if (currentPlanStr !== newPlanStr) {
      setCurrentTaskPlan(taskPlan);
    }
  }, [taskPlan]);

  return {
    taskPlan: currentTaskPlan,
  };
}

// Helper function to deeply compare task plans
function taskPlansEqual(a?: TaskPlan, b?: TaskPlan): boolean {
  if (a === b) return true;
  if (!a || !b) return false;

  // Compare key properties that matter for UI updates
  if (a.activeTaskIndex !== b.activeTaskIndex) return false;
  if (a.tasks.length !== b.tasks.length) return false;

  // Compare active task details
  const activeTaskA = a.tasks[a.activeTaskIndex];
  const activeTaskB = b.tasks[b.activeTaskIndex];

  if (!activeTaskA || !activeTaskB) return false;
  if (activeTaskA.activeRevisionIndex !== activeTaskB.activeRevisionIndex)
    return false;

  // Compare plan items completion status in the active revision
  const activeRevA = activeTaskA.planRevisions[activeTaskA.activeRevisionIndex];
  const activeRevB = activeTaskB.planRevisions[activeTaskB.activeRevisionIndex];

  if (!activeRevA || !activeRevB) return false;
  if (activeRevA.plans.length !== activeRevB.plans.length) return false;

  // Check if any plan item completion status changed
  for (let i = 0; i < activeRevA.plans.length; i++) {
    if (activeRevA.plans[i].completed !== activeRevB.plans[i].completed) {
      return false;
    }
  }

  return true;
}

// Hook that gets the active task plan from the thread status polling system
export function useActiveTaskPlan(
  displayThreadTaskPlan?: TaskPlan,
  programmerSession?: ManagerGraphState["programmerSession"],
  displayThreadId?: string,
  plannerThreadId?: string,
) {
  const [activeTaskPlan, setActiveTaskPlan] = useState<TaskPlan>();

  // Use the existing thread status polling for the display thread
  const { statusData: displayThreadStatusData } = useThreadStatus(
    displayThreadId || "",
    { includeTaskPlan: true },
  ) as any;

  // Use the existing thread status polling for the programmer thread when active
  const { statusData: programmerStatusData } = useThreadStatus(
    programmerSession?.threadId || "",
    { enabled: !!programmerSession?.threadId, includeTaskPlan: true },
  ) as any;

  // Use the existing thread status polling for the planner thread when programmer is active
  // but doesn't have its own task plan yet (handles transition period)
  const { statusData: plannerStatusData } = useThreadStatus(
    plannerThreadId || "",
    { enabled: !!plannerThreadId && !!programmerSession?.threadId, includeTaskPlan: true },
  ) as any;

  // Memoize the task plan selection to prevent unnecessary re-renders
  const selectedTaskPlan = useMemo(() => {
    // Priority 1: If programmer has a task plan, use it
    if (programmerSession?.threadId && programmerStatusData?.taskPlan) {
      return programmerStatusData.taskPlan;
    }
    // Priority 2: If programmer is active but doesn't have task plan yet, use planner's task plan
    if (programmerSession?.threadId && plannerStatusData?.taskPlan) {
      return plannerStatusData.taskPlan;
    }
    // Priority 3: Use display thread status data task plan
    if (displayThreadStatusData?.taskPlan) {
      return displayThreadStatusData.taskPlan;
    }
    // Priority 4: Use display thread task plan as fallback
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

  // Only update state when the task plan actually changes
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
