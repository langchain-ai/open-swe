import { Thread } from "@langchain/langgraph-sdk";
import { getMessageContentString } from "@open-swe/shared/messages";
import { GraphState, TaskPlan } from "@open-swe/shared/agent-mojo/types";
import { getActivePlanItems } from "@open-swe/shared/agent-mojo/tasks";

export function computeThreadTitle(
  taskPlan: TaskPlan | undefined,
  fallbackTitle: string,
): string {
  if (taskPlan?.tasks && taskPlan.tasks.length > 0) {
    const firstTaskTitle = taskPlan.tasks[0]?.title;
    if (firstTaskTitle && firstTaskTitle.trim()) {
      return firstTaskTitle;
    }
  }
  return fallbackTitle;
}

export function getThreadTitle<State extends Record<string, any> = GraphState>(
  thread: Thread<State>,
): string {
  // Prefer an explicit title if one was set in the thread state
  const explicitTitle = (thread as any)?.values?.title as string | undefined;
  if (explicitTitle && explicitTitle.trim()) {
    return explicitTitle.trim();
  }

  const messages = thread?.values?.messages;
  if (!messages?.length || !messages[0]?.content) {
    return `Thread ${thread.thread_id.substring(0, 8)}`;
  }
  const threadTitle = getMessageContentString(messages[0].content);
  return threadTitle;
}

export function getThreadTasks<State extends Record<string, any> = GraphState>(
  thread: Thread<State>,
): {
  totalTasks: number;
  completedTasks: number;
} {
  if (!thread.values || !thread.values?.taskPlan) {
    return {
      totalTasks: 0,
      completedTasks: 0,
    };
  }
  const activePlanItems = getActivePlanItems(thread.values.taskPlan);
  const totalTasks = activePlanItems.length;
  const completedTasks = activePlanItems.filter((p) => p.completed).length;
  return {
    totalTasks,
    completedTasks,
  };
}
