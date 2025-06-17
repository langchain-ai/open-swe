import type { Thread, ThreadDisplayInfo } from "@/types";

// Utility function to convert between Thread and ThreadDisplayInfo
export function threadToDisplayInfo(thread: Thread): ThreadDisplayInfo {
  const values = thread.values;
  const currentTask = values.taskPlan.tasks[values.taskPlan.activeTaskIndex];
  const completedTasks = values.taskPlan.tasks.filter(
    (t) => t.completed,
  ).length;

  // Determine UI status from thread status and task completion
  let uiStatus: ThreadDisplayInfo["status"];
  switch (thread.status) {
    case "busy":
      uiStatus = "running";
      break;
    case "idle":
      uiStatus =
        completedTasks === values.taskPlan.tasks.length
          ? "completed"
          : "pending";
      break;
    case "error":
      uiStatus = "failed";
      break;
    case "interrupted":
      uiStatus = "pending";
      break;
    default:
      uiStatus = "pending";
  }

  // Calculate time since last update
  const lastUpdate = new Date(thread.updated_at);
  const now = new Date();
  const diffMs = now.getTime() - lastUpdate.getTime();
  const diffMins = Math.floor(diffMs / (1000 * 60));
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  let lastActivity: string;
  if (diffMins < 1) {
    lastActivity = "just now";
  } else if (diffMins < 60) {
    lastActivity = `${diffMins} min ago`;
  } else if (diffHours < 24) {
    lastActivity = `${diffHours} hour${diffHours > 1 ? "s" : ""} ago`;
  } else {
    lastActivity = `${diffDays} day${diffDays > 1 ? "s" : ""} ago`;
  }

  return {
    id: thread.thread_id,
    title: currentTask?.request || "Untitled Task",
    status: uiStatus,
    lastActivity,
    taskCount: values.taskPlan.tasks.length,
    repository: `${values.targetRepository.owner}/${values.targetRepository.repo}`,
    branch: values.targetRepository.branch || "main",
    githubIssue: values.githubIssueId
      ? {
          number: values.githubIssueId,
          url: `https://github.com/${values.targetRepository.owner}/${values.targetRepository.repo}/issues/${values.githubIssueId}`,
        }
      : undefined,
    // Note: PR info would need to be added to GraphAnnotation or derived from metadata
  };
}
