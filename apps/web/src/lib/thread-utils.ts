import { formatDistanceToNow } from "date-fns";
import { Thread } from "@langchain/langgraph-sdk";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { ThreadMetadata } from "@/components/v2/types";
import { getThreadTitle } from "./thread";

/**
 * Calculate human-readable last activity time from thread updated_at timestamp
 */
export function calculateLastActivity(updatedAt: string): string {
  return formatDistanceToNow(new Date(updatedAt), { addSuffix: true });
}

/**
 * Converts raw manager threads to ThreadMetadata objects for UI display
 */
export function threadsToMetadata(
  threads: Thread<ManagerGraphState>[],
): ThreadMetadata[] {
  return threads.map((thread): ThreadMetadata => {
    const values = thread.values;

    return {
      id: thread.thread_id,
      title: getThreadTitle(thread),
      lastActivity: calculateLastActivity(thread.updated_at),
      taskCount: values?.taskPlan?.tasks.length ?? 0,
      repository: values?.targetRepository
        ? `${values.targetRepository.owner}/${values.targetRepository.repo}`
        : "",
      branch: values?.targetRepository?.branch || "main",
      taskPlan: values?.taskPlan,
      status: "idle" as const, // Default status - consumers can override with real status
      githubIssue: values?.githubIssueId
        ? {
            number: values?.githubIssueId,
            url: `https://github.com/${values?.targetRepository?.owner}/${values?.targetRepository?.repo}/issues/${values?.githubIssueId}`,
          }
        : undefined,
    };
  });
}
