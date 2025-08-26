import { v4 as uuidv4 } from "uuid";
import { PlanItem, Task, TaskPlan, PlanRevision } from "./types.js";

/**
 * Creates a new task with the provided plan items.
 * Can either add to an existing TaskPlan or create a brand new one.
 *
 * @param request The original user request text that initiated this task
 * @param planItems The plan items to include in the new task
 * @param options Optional existing TaskPlan to add the new task to
 * @param options.parentTaskId Optional ID of a parent task if this task is derived from another
 * @param options.existingTaskPlan Optional existing TaskPlan to add the new task to
 * @returns The updated TaskPlan with the new task added
 */
export function createNewTask(
  request: string,
  title: string,
  planItems: PlanItem[],
  options?: {
    existingTaskPlan?: TaskPlan;
    parentTaskId?: string;
  },
): TaskPlan {
  const { existingTaskPlan, parentTaskId } = options ?? {};

  // Create the initial plan revision
  const initialRevision: PlanRevision = {
    revisionIndex: 0,
    plans: planItems,
    createdAt: Date.now(),
    createdBy: "agent",
  };

  // Create the new task
  const newTask: Task = {
    id: uuidv4(),
    taskIndex: existingTaskPlan ? existingTaskPlan.tasks.length : 0,
    request,
    title,
    createdAt: Date.now(),
    completed: false,
    planRevisions: [initialRevision],
    activeRevisionIndex: 0,
    parentTaskId,
  };

  // If there's an existing task plan, add the new task to it
  if (existingTaskPlan) {
    return {
      tasks: [...existingTaskPlan.tasks, newTask],
      activeTaskIndex: existingTaskPlan.tasks.length, // Set the new task as active
    };
  }

  // Otherwise create a new task plan with just this task
  return {
    tasks: [newTask],
    activeTaskIndex: 0,
  };
}

/**
 * Updates the plan items for an existing task by creating a new revision.
 *
 * @param taskPlan The current task plan
 * @param taskId The ID of the task to update
 * @param planItems The new plan items
 * @param createdBy Who created this revision ('agent' or 'user')
 * @returns The updated TaskPlan with the new revision
 * @throws Error if the task ID doesn't exist
 */
export function updateTaskPlanItems(
  taskPlan: TaskPlan,
  taskId: string,
  planItems: PlanItem[],
  createdBy: "agent" | "user" = "agent",
): TaskPlan {
  // Find the task to update
  const taskIndex = taskPlan.tasks.findIndex((task) => task.id === taskId);

  if (taskIndex === -1) {
    throw new Error(`Task with ID ${taskId} not found`);
  }

  const task = taskPlan.tasks[taskIndex];

  // Create a new revision with the updated plan items
  const newRevision: PlanRevision = {
    revisionIndex: task.planRevisions.length,
    plans: planItems,
    createdAt: Date.now(),
    createdBy,
  };

  // Create an updated task with the new revision
  const updatedTask: Task = {
    ...task,
    planRevisions: [...task.planRevisions, newRevision],
    activeRevisionIndex: task.planRevisions.length, // Set the new revision as active
  };

  // Create a new array of tasks with the updated task
  const updatedTasks = [...taskPlan.tasks];
  updatedTasks[taskIndex] = updatedTask;

  // Return the updated task plan
  return {
    ...taskPlan,
    tasks: updatedTasks,
  };
}

/**
 * Adds a pull request number to the active task in the task plan.
 *
 * @param taskPlan The task plan to update
 * @param pullRequestNumber The pull request number to add
 * @returns The updated task plan
 * @throws Error if the task ID doesn't exist
 */
export function addPullRequestNumberToActiveTask(
  taskPlan: TaskPlan,
  pullRequestNumber: number,
): TaskPlan {
  const activeTaskIndex = taskPlan.activeTaskIndex;
  const activeTask = taskPlan.tasks[activeTaskIndex];

  if (!activeTask) {
    throw new Error(`Task with index ${activeTaskIndex} not found`);
  }

  // Create an updated task marked as completed
  const updatedTask: Task = {
    ...activeTask,
    pullRequestNumber,
  };

  // Create a new array of tasks with the updated task
  const updatedTasks = [...taskPlan.tasks];
  updatedTasks[activeTaskIndex] = updatedTask;

  // Return the updated task plan
  return {
    ...taskPlan,
    tasks: updatedTasks,
  };
}

/**
 * Gets the pull request number from the active task in the task plan.
 *
 * @param taskPlan The task plan
 * @returns The pull request number of the active task, or undefined if the active task has no pull request number
 */
export function getPullRequestNumberFromActiveTask(
  taskPlan: TaskPlan,
): number | undefined {
  const activeTask = getActiveTask(taskPlan);
  return activeTask.pullRequestNumber;
}

/**
 * Helper function to get the active task from a TaskPlan
 *
 * @param taskPlan The task plan
 * @returns The currently active task
 * @throws Error if there are no tasks
 */
export function getActiveTask(taskPlan: TaskPlan): Task {
  if (taskPlan.tasks.length === 0) {
    throw new Error("No tasks available");
  }

  return taskPlan.tasks[taskPlan.activeTaskIndex];
}

/**
 * Helper function to get the active plan items for the active task
 *
 * @param taskPlan The task plan
 * @returns The currently active plan items
 * @throws Error if there are no tasks or no plan revisions
 */
export function getActivePlanItems(taskPlan: TaskPlan): PlanItem[] {
  const activeTask = getActiveTask(taskPlan);

  if (activeTask.planRevisions.length === 0) {
    throw new Error("No plan revisions available for the active task");
  }

  return activeTask.planRevisions[activeTask.activeRevisionIndex].plans;
}

/**
 * Marks a specific plan item as completed and adds a summary.
 * This operation modifies the current active revision directly and does NOT create a new revision.
 *
 * @param taskPlan The current task plan
 * @param taskId The ID of the task containing the plan item
 * @param planItemIndex The `index` property of the plan item to mark as completed
 * @param summary Optional summary of the completed plan item. If undefined, existing summary is preserved.
 * @returns The updated TaskPlan
 * @throws Error if the task or plan item is not found, or if no active revision exists.
 */
export function completePlanItem(
  taskPlan: TaskPlan,
  taskId: string,
  planItemIndex: number,
  summary?: string,
): TaskPlan {
  const taskIndexInPlan = taskPlan.tasks.findIndex(
    (task) => task.id === taskId,
  );

  if (taskIndexInPlan === -1) {
    throw new Error(`Task with ID ${taskId} not found in task plan`);
  }

  const originalTask = taskPlan.tasks[taskIndexInPlan];

  const activeRevisionIndex = originalTask.activeRevisionIndex;

  // Ensure there's a planRevisions array and the activeRevisionIndex is valid
  if (
    !originalTask.planRevisions ||
    activeRevisionIndex < 0 ||
    activeRevisionIndex >= originalTask.planRevisions.length
  ) {
    throw new Error(
      `Invalid active revision index (${activeRevisionIndex}) for task ${taskId}`,
    );
  }

  const originalActiveRevision =
    originalTask.planRevisions[activeRevisionIndex];

  // This check should be covered by the index check, but reinforces intent
  if (!originalActiveRevision) {
    // This case implies an issue with activeRevisionIndex or planRevisions structure
    throw new Error(
      `Active revision (index ${activeRevisionIndex}) not found for task ${taskId}`,
    );
  }

  const planItemToUpdateActualIndexInPlansArray =
    originalActiveRevision.plans.findIndex(
      (item) => item.index === planItemIndex,
    );

  if (planItemToUpdateActualIndexInPlansArray === -1) {
    throw new Error(
      `Plan item with .index ${planItemIndex} not found in active revision (index ${activeRevisionIndex}) of task ${taskId}`,
    );
  }

  // Create a new 'plans' array with the specific item updated
  const updatedPlansForRevision = originalActiveRevision.plans.map((item) => {
    if (item.index === planItemIndex) {
      const newSummary = summary !== undefined ? summary : item.summary;
      return { ...item, completed: true, summary: newSummary };
    }
    return item;
  });

  // Create a new 'PlanRevision' object for the active revision, with the updated 'plans'
  const updatedActiveRevision: PlanRevision = {
    ...originalActiveRevision,
    plans: updatedPlansForRevision,
  };

  // Create a new 'planRevisions' array, replacing the active revision with the updated one
  const updatedPlanRevisions = [...originalTask.planRevisions];
  updatedPlanRevisions[activeRevisionIndex] = updatedActiveRevision;

  // Create a new 'Task' object with the updated 'planRevisions'
  const updatedTask: Task = {
    ...originalTask,
    planRevisions: updatedPlanRevisions,
  };

  // Create a new 'tasks' array for the TaskPlan, replacing the updated task
  const updatedTasksArray = [...taskPlan.tasks];
  updatedTasksArray[taskIndexInPlan] = updatedTask;

  // Return the new TaskPlan object
  return {
    ...taskPlan,
    tasks: updatedTasksArray,
  };
}

/**
 * Marks a task as completed
 *
 * @param taskPlan The current task plan
 * @param taskId The ID of the task to mark as completed
 * @param summary Optional summary of the completed task
 * @returns The updated TaskPlan
 */
export function completeTask(
  taskPlan: TaskPlan,
  taskId: string,
  summary?: string,
): TaskPlan {
  const taskIndex = taskPlan.tasks.findIndex((task) => task.id === taskId);

  if (taskIndex === -1) {
    throw new Error(`Task with ID ${taskId} not found`);
  }

  const task = taskPlan.tasks[taskIndex];

  // Create an updated task marked as completed
  const updatedTask: Task = {
    ...task,
    completed: true,
    completedAt: Date.now(),
    summary,
  };

  // Create a new array of tasks with the updated task
  const updatedTasks = [...taskPlan.tasks];
  updatedTasks[taskIndex] = updatedTask;

  // Return the updated task plan
  return {
    ...taskPlan,
    tasks: updatedTasks,
  };
}
