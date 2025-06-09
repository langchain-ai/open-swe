import { PlanItem } from "@open-swe/shared/open-swe/types";

export const PLAN_PROMPT = `## Completed Tasks
{COMPLETED_TASKS}

## Remaining Tasks
(This list does not include the current task)
{REMAINING_TASKS}

## Current Task
{CURRENT_TASK}`;

/**
 * Formats a plan for use in a prompt.
 * @param plan The plan to format
 * @param options Options for formatting the plan
 * @param options.useLastCompletedTask Whether to use the last completed task as the current task
 * @param options.includeSummaries Whether to include summaries of completed tasks
 * @returns The formatted plan
 */
export function formatPlanPrompt(
  plan: PlanItem[],
  options?: {
    useLastCompletedTask?: boolean;
    includeSummaries?: boolean;
  },
): string {
  let completedTasks = plan.filter((p) => p.completed);
  let remainingTasks = plan.filter((p) => !p.completed);
  let currentTask: PlanItem | undefined;
  if (options?.useLastCompletedTask) {
    currentTask = completedTasks.sort((a, b) => a.index - b.index)[0];
    // Remove the current task from the completed tasks list:
    completedTasks = completedTasks.filter(
      (p) => p.index !== currentTask?.index,
    );
  } else {
    currentTask = remainingTasks.sort((a, b) => a.index - b.index)[0];
    // Remove the current task from the remaining tasks list:
    remainingTasks = remainingTasks.filter(
      (p) => p.index !== currentTask?.index,
    );
  }

  return PLAN_PROMPT.replace(
    "{COMPLETED_TASKS}",
    completedTasks?.length
      ? options?.includeSummaries
        ? formatPlanPromptWithSummaries(completedTasks)
        : completedTasks
            .map(
              (task) =>
                `<completed-task index="${task.index}">${task.plan}</completed-task>`,
            )
            .join("\n")
      : "No completed tasks.",
  )
    .replace(
      "{REMAINING_TASKS}",
      remainingTasks?.length
        ? remainingTasks
            .map(
              (task) =>
                `<remaining-task index="${task.index}">${task.plan}</remaining-task>`,
            )
            .join("\n")
        : "No remaining tasks.",
    )
    .replace(
      "{CURRENT_TASK}",
      `<current-task index="${currentTask?.index}">${currentTask?.plan || "No current task found."}</current-task>`,
    );
}

export function formatPlanPromptWithSummaries(plan: PlanItem[]): string {
  return plan
    .map(
      (p) =>
        `<${p.completed ? "completed-" : ""}task index="${p.index}">\n${p.plan}\n  <task-summary>\n${p.summary || "No task summary found"}\n  </task-summary>\n</${p.completed ? "completed-" : ""}task>`,
    )
    .join("\n");
}
