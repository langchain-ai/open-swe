import { useStreamContext } from "@/providers/Stream";
import { TaskPlan } from "@open-swe/shared/open-swe/types";
import { useEffect, useState } from "react";

export function useTaskPlan() {
  const { values } = useStreamContext();
  const [taskPlan, setTaskPlan] = useState<TaskPlan>();

  useEffect(() => {
    const currentPlanStr = JSON.stringify(taskPlan, null, 2);
    const newPlanStr = JSON.stringify(values?.plan, null, 2);
    if (currentPlanStr !== newPlanStr) {
      setTaskPlan(values?.plan);
    }
  }, [values?.plan]);

  return {
    taskPlan,
  };
}
