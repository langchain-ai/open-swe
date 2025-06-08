import { PlanItem } from "@open-swe/shared/open-swe/types";

export function getCurrentPlanItem(plan: PlanItem[]): PlanItem {
  return (
    plan.filter((p) => !p.completed).sort((a, b) => a.index - b.index)?.[0] || {
      plan: "No current task found.",
      index: -1,
      completed: true,
      summary: "",
    }
  );
}

/**
 * Gets the completed plan items for the given plan.
 * @param plan The list of plan items to get the completed plan items for.
 * @returns The list of completed plan items.
 */
export function getCompletedPlanItems(plan: PlanItem[]): PlanItem[] {
  return plan.filter((p) => p.completed);
}

/**
 * Gets the remaining plan items for the given plan.
 * @param plan The list of plan items to get the remaining plan items for.
 * @param includeCurrentPlanItem Whether to include the current plan item in the remaining plan items.
 *        Defaults to false.
 * @returns The list of remaining plan items.
 */
export function getRemainingPlanItems(
  plan: PlanItem[],
  includeCurrentPlanItem = false,
): PlanItem[] {
  return plan
    .filter(
      (p) =>
        !p.completed &&
        (includeCurrentPlanItem || p.index !== getCurrentPlanItem(plan).index),
    )
    ?.sort((a, b) => a.index - b.index);
}
