import { PlanItem } from "../types.js";

export const PLAN_PROMPT = `## Completed Tasks
{COMPLETED_TASKS}

## Remaining Tasks
{REMAINING_TASKS}

## Current Task
{CURRENT_TASK}`;

/**
 * Formats a plan for use in a prompt.
 * @param plan The plan to format
 * @param options Options for formatting the plan
 * @param options.useLastCompletedTask Whether to use the last completed task as the current task
 * @returns The formatted plan
 */
export function formatPlanPrompt(
  plan: PlanItem[],
  options?: {
    useLastCompletedTask?: boolean;
  },
): string {
  const completedTasks = plan.filter((p) => p.completed);
  const remainingTasks = plan.filter((p) => !p.completed);
  const currentTask = options?.useLastCompletedTask
    ? completedTasks.sort((a, b) => a.index - b.index)[0]
    : remainingTasks.sort((a, b) => a.index - b.index)[0];

  return PLAN_PROMPT.replace(
    "{COMPLETED_TASKS}",
    completedTasks?.length
      ? completedTasks.map((task) => `${task.index}. ${task.plan}`).join("\n")
      : "No completed tasks.",
  )
    .replace(
      "{REMAINING_TASKS}",
      remainingTasks?.length
        ? remainingTasks.map((task) => `${task.index}. ${task.plan}`).join("\n")
        : "No remaining tasks.",
    )
    .replace("{CURRENT_TASK}", currentTask?.plan || "No current task.");
}
