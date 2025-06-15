import { createLogger, LogLevel } from "./logger.js";
import { TaskPlan } from "@open-swe/shared/open-swe/types";

const logger = createLogger(LogLevel.INFO, "TaskStringExtraction");

export const TASK_OPEN_TAG = "<oap-do-not-edit-task-plan>";
export const TASK_CLOSE_TAG = "</oap-do-not-edit-task-plan>";

export function typeNarrowTaskPlan(taskPlan: unknown): taskPlan is TaskPlan {
  return !!(
    typeof taskPlan === "object" &&
    !Array.isArray(taskPlan) &&
    taskPlan &&
    "tasks" in taskPlan &&
    Array.isArray(taskPlan.tasks) &&
    "activeTaskIndex" in taskPlan &&
    typeof taskPlan.activeTaskIndex === "number"
  );
}

export function extractTasksFromIssueContent(content: string): TaskPlan | null {
  if (!content.includes(TASK_OPEN_TAG) || !content.includes(TASK_CLOSE_TAG)) {
    return null;
  }
  const taskPlanString = content.substring(
    content.indexOf(TASK_OPEN_TAG) + TASK_OPEN_TAG.length,
    content.indexOf(TASK_CLOSE_TAG),
  );
  try {
    const parsedTaskPlan = JSON.parse(taskPlanString);
    if (!typeNarrowTaskPlan(parsedTaskPlan)) {
      throw new Error("Invalid task plan parsed.");
    }
    return parsedTaskPlan;
  } catch (e) {
    logger.error("Failed to parse task plan", {
      taskPlanString,
      ...(e instanceof Error && {
        name: e.name,
        message: e.message,
        stack: e.stack,
      }),
    });
    return null;
  }
}
