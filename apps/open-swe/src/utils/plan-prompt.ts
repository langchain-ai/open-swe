import { PlanItem } from "@open-swe/shared/open-swe/types";

export const PLAN_PROMPT = `<completed_tasks>
  {COMPLETED_TASKS}
</completed_tasks>

<remaining_tasks>
  (This list does not include the current task)
  {REMAINING_TASKS}
</remaining_tasks>

  {CURRENT_TASK}
`;

/**
 * Formats a plan for use in a prompt.
 * @param taskPlan The plan to format
 * @param options Options for formatting the plan
 * @param options.useLastCompletedTask Whether to use the last completed task as the current task
 * @param options.includeSummaries Whether to include summaries of completed tasks
 * @returns The formatted plan
 */
export function formatPlanPrompt(
  taskPlan: PlanItem[],
  options?: {
    useLastCompletedTask?: boolean;
    includeSummaries?: boolean;
  },
): string {
  let completedTasks = taskPlan.filter((p) => p.completed);
  let remainingTasks = taskPlan.filter((p) => !p.completed);
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
                `<completed_task index="${task.index}">\n${task.plan}\n</completed_task>`,
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
                `<remaining_task index="${task.index}">\n${task.plan}\n</remaining_task>`,
            )
            .join("\n")
        : "No remaining tasks.",
    )
    .replace(
      "{CURRENT_TASK}",
      `<current_task index="${currentTask?.index}">\n${currentTask?.plan || "No current task found."}\n</current_task>`,
    );
}

export function formatPlanPromptWithSummaries(taskPlan: PlanItem[]): string {
  return taskPlan
    .map(
      (p) =>
        `<${p.completed ? "completed_" : ""}task index="${p.index}">\n${p.plan}\n  <task_summary>\n${p.summary || "No task summary found"}\n  </task_summary>\n</${p.completed ? "completed_" : ""}task>`,
    )
    .join("\n");
}
