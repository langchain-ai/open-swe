import {
  Bug,
  CheckCircle,
  GitBranch,
  GitPullRequest,
  Loader2,
  AlertCircle,
  Pause,
  XCircle,
  Clock,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { useRouter } from "next/navigation";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Skeleton } from "../ui/skeleton";
import { ThreadMetadata } from "./types";
import { ThreadUIStatus } from "@/lib/schemas/thread-status";
import { cn } from "@/lib/utils";
import { TaskPlan } from "@open-swe/shared/open-swe/types";

interface ThreadCardProps {
  thread: ThreadMetadata;
  status?: ThreadUIStatus;
  statusLoading?: boolean;
  taskPlan?: TaskPlan;
}

export function ThreadCard({
  thread,
  status,
  statusLoading,
  taskPlan,
}: ThreadCardProps) {
  const router = useRouter();

  const isStatusLoading = statusLoading && !status;
  const displayStatus = status || ("idle" as ThreadUIStatus);

  // Calculate task progress using the same logic as ProgressBar
  const getTaskProgress = () => {
    if (!taskPlan || !taskPlan.tasks.length) {
      return { currentTaskIndex: 0, totalTasks: 0, hasActivePlan: false };
    }

    const currentTask = taskPlan.tasks[taskPlan.activeTaskIndex];
    if (!currentTask || !currentTask.planRevisions.length) {
      return { currentTaskIndex: 0, totalTasks: 0, hasActivePlan: false };
    }

    const activeRevision =
      currentTask.planRevisions[currentTask.activeRevisionIndex];
    const planItems = [...activeRevision.plans].sort(
      (a, b) => a.index - b.index,
    );

    // Find the current task (lowest index among uncompleted tasks)
    const currentTaskIndex = planItems
      .filter((item) => !item.completed)
      .reduce(
        (min, item) => (item.index < min ? item.index : min),
        Number.POSITIVE_INFINITY,
      );

    const displayCurrentIndex =
      currentTaskIndex === Number.POSITIVE_INFINITY
        ? planItems.length // All tasks completed
        : currentTaskIndex; // +1 for 1-based display

    return {
      currentTaskIndex: displayCurrentIndex,
      totalTasks: planItems.length,
      hasActivePlan: true,
    };
  };

  const { currentTaskIndex, totalTasks, hasActivePlan } = getTaskProgress();

  const getStatusColor = (status: ThreadUIStatus) => {
    switch (status) {
      case "running":
        return "dark:bg-blue-950 bg-blue-100 dark:text-blue-400 text-blue-700";
      case "completed":
        return "dark:bg-green-950 bg-green-100 dark:text-green-400 text-green-700";
      case "error":
        return "dark:bg-red-950 bg-red-100 dark:text-red-400 text-red-700";
      case "paused":
        return "dark:bg-yellow-950 bg-yellow-100 dark:text-yellow-400 text-yellow-700";
      case "failed":
        return "dark:bg-red-950 bg-red-100 dark:text-red-400 text-red-700";
      case "pending":
        return "dark:bg-yellow-950 bg-yellow-100 dark:text-yellow-400 text-yellow-700";
      default:
        return "dark:bg-gray-800 bg-gray-200 dark:text-gray-400 text-gray-700";
    }
  };

  const getStatusIcon = (status: ThreadUIStatus) => {
    switch (status) {
      case "running":
        return <Loader2 className="h-4 w-4 animate-spin" />;
      case "completed":
        return <CheckCircle className="h-4 w-4" />;
      case "error":
        return <AlertCircle className="h-4 w-4" />;
      case "idle":
        return <Clock className="h-4 w-4" />;
      case "paused":
        return <Pause className="h-4 w-4" />;
      case "failed":
        return <XCircle className="h-4 w-4" />;
      default:
        return null;
    }
  };

  const getPRStatusColor = (status: string) => {
    switch (status) {
      case "merged":
        return "dark:text-purple-400 text-purple-600";
      case "open":
        return "dark:text-green-400 text-green-600";
      case "draft":
        return "dark:text-gray-400 text-gray-600";
      case "closed":
        return "dark:text-red-400 text-red-600";
      default:
        return "dark:text-gray-400 text-gray-600";
    }
  };

  return (
    <Card
      key={thread.id}
      className="border-border bg-card hover:bg-muted cursor-pointer px-0 py-3 transition-shadow hover:shadow-lg dark:bg-gray-950"
      onClick={() => {
        router.push(`/chat/${thread.id}`);
      }}
    >
      <CardHeader>
        <div className="flex items-start justify-between">
          <div className="min-w-0 flex-1">
            <CardTitle className="text-foreground truncate text-sm font-medium">
              {thread.title}
            </CardTitle>
            <div className="mt-1 flex items-center gap-1">
              <GitBranch className="text-muted-foreground h-2 w-2" />
              <span className="text-muted-foreground truncate text-xs">
                {thread.repository}
              </span>
            </div>
          </div>
          <Badge
            variant="secondary"
            className={cn(
              "text-xs",
              isStatusLoading
                ? "bg-gray-200 text-gray-600 dark:bg-gray-800 dark:text-gray-400"
                : getStatusColor(displayStatus),
            )}
          >
            <div className="flex items-center gap-1">
              {isStatusLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                getStatusIcon(displayStatus)
              )}
              <span className="capitalize">
                {isStatusLoading ? "Loading..." : displayStatus}
              </span>
            </div>
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground text-xs">
              {!taskPlan || !taskPlan.tasks.length
                ? "No active plan"
                : `${currentTaskIndex}/${totalTasks} tasks`}
            </span>
            <span className="text-muted-foreground text-xs">•</span>
            <span className="text-muted-foreground text-xs">
              {thread.lastActivity}
            </span>
          </div>
          <div className="flex items-center gap-1">
            {thread.githubIssue && (
              <Button
                variant="ghost"
                size="sm"
                className="text-muted-foreground hover:text-foreground h-5 w-5 p-0"
                onClick={(e) => {
                  e.stopPropagation();
                  window.open(thread.githubIssue!.url, "_blank");
                }}
              >
                <Bug className="h-3 w-3" />
              </Button>
            )}
            {thread.pullRequest && (
              <Button
                variant="ghost"
                size="sm"
                className={cn(
                  "h-5 w-5 p-0 hover:text-gray-300",
                  getPRStatusColor(thread.pullRequest.status),
                )}
                onClick={(e) => {
                  e.stopPropagation();
                  window.open(thread.pullRequest!.url, "_blank");
                }}
              >
                <GitPullRequest className="h-3 w-3" />
              </Button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export function ThreadCardLoading() {
  return (
    <Card className="border-border bg-card px-0 py-3 dark:bg-gray-950">
      <CardHeader>
        <div className="flex items-start justify-between">
          <div className="min-w-0 flex-1">
            <CardTitle className="text-foreground truncate text-sm font-medium">
              <Skeleton className="h-5 w-48" />
            </CardTitle>
            <div className="mt-1 flex items-center gap-1">
              <Skeleton className="h-3 w-3 rounded-full" />
              <Skeleton className="h-3 w-32" />
            </div>
          </div>
          <Skeleton className="h-6 w-24 rounded-full" />
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Skeleton className="h-3 w-16" />
            <Skeleton className="h-3 w-1" />
            <Skeleton className="h-3 w-24" />
          </div>
          <div className="flex items-center gap-1">
            <Skeleton className="h-5 w-5 rounded-full" />
            <Skeleton className="h-5 w-5 rounded-full" />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
