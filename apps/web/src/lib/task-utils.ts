import { TaskWithStatus, TaskStatus } from "@/types/index";

export const formatTaskTitle = (
  plan: string,
  maxLength: number = 60,
): string => {
  if (!plan) return "Untitled Task";
  let title = plan.trim();
  title = title.replace(/(^\w|\.\s+\w)/g, (match) => match.toUpperCase());
  if (title.length > maxLength) {
    const truncated = title.substring(0, maxLength);
    const lastSpace = truncated.lastIndexOf(" ");
    if (lastSpace > maxLength * 0.7) {
      title = truncated.substring(0, lastSpace);
    } else {
      title = truncated + "...";
    }
  }

  return title;
};

export const getCurrentTask = (
  plan: TaskWithStatus[],
): TaskWithStatus | null => {
  return (
    plan.filter((p) => !p.completed).sort((a, b) => a.index - b.index)[0] ||
    null
  );
};

export const computeTaskStatus = (
  task: TaskWithStatus,
  currentTask: TaskWithStatus | null,
  isLoading: boolean,
  hasError: boolean,
  hasInterrupt: boolean,
): TaskStatus => {
  if (task.completed) {
    return "done";
  }

  const isCurrentTask = currentTask?.index === task.index;

  if (isCurrentTask) {
    if (hasError) {
      return "error";
    }

    if (hasInterrupt) {
      return "interrupted";
    }

    if (isLoading) {
      return "running";
    }
  }

  return "interrupted";
};
