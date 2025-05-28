import { PlanItem } from "../types.js";

export function getCurrentTask(plan: PlanItem[]) {
  return (
    plan.filter((p) => !p.completed).sort((a, b) => a.index - b.index)?.[0] || {
      plan: "No current task found.",
      index: -1,
      completed: true,
      summary: "",
    }
  );
}
