import { PlanItem } from "../types.js";

export function getCurrentTask(plan: PlanItem[]) {
  return (
    plan.filter((p) => !p.completed).sort((a, b) => a.index - b.index)?.[0] ||
    "No current task found."
  );
}
