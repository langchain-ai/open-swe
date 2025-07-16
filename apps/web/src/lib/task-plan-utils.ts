import { TaskPlan } from "@open-swe/shared/open-swe/types";

/**
 * Deeply compares two task plans for equality, focusing on properties that matter for UI updates.
 * This is optimized to avoid unnecessary re-renders by only checking relevant fields.
 */
export function taskPlansEqual(a?: TaskPlan, b?: TaskPlan): boolean {
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
