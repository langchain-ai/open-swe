import { PlanItem } from "../types.js";

export const PLAN_PROMPT = `## Completed Tasks
{COMPLETED_TASKS}

## Remaining Tasks
{REMAINING_TASKS}

## Current Task
{CURRENT_TASK}`;

export function formatPlanPrompt(plan: PlanItem[]): string {
  const completedTasks = plan.filter((p) => p.completed);
  const remainingTasks = plan.filter((p) => !p.completed);
  const currentTask = remainingTasks.sort((a, b) => a.index - b.index)[0];

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
