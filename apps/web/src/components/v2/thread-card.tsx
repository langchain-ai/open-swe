import {
  Bug,
  CheckCircle,
  GitBranch,
  GitPullRequest,
  AlertCircle,
  Pause,
  XCircle,
  Clock,
  Loader2,
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
import { getActivePlanItems } from "@open-swe/shared/open-swe/tasks";
import { InlineMarkdownText } from "../thread/markdown-text";
import { computeThreadTitle } from "@/lib/thread";

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

  const threadTitle = computeThreadTitle(taskPlan, thread.title);
  const isStatusLoading = statusLoading && !status;
  const displayStatus = status || ("idle" as ThreadUIStatus);

  const getTaskProgress = () => {
    if (!taskPlan || !taskPlan.tasks.length) {
      return { currentTaskIndex: 0, totalTasks: 0 };
    }

    try {
      const planItems = getActivePlanItems(taskPlan);
      const sortedPlanItems = [...planItems].sort((a, b) => a.index - b.index);

      const currentTaskIndex = sortedPlanItems
        .filter((item) => !item.completed)
        .reduce(
          (min, item) => (item.index < min ? item.index : min),
          Number.POSITIVE_INFINITY,
        );

      const displayCurrentIndex =
        currentTaskIndex === Number.POSITIVE_INFINITY
          ? sortedPlanItems.length
          : currentTaskIndex;

      return {
        currentTaskIndex: displayCurrentIndex,
        totalTasks: sortedPlanItems.length,
      };
    } catch (error) {
      return { currentTaskIndex: 0, totalTasks: 0 };
    }
  };

  const { currentTaskIndex, totalTasks } = getTaskProgress();

  const getStatusColor = (status: ThreadUIStatus) => {
    switch (status) {
      case "running":
        return "bg-blue-100 dark:bg-blue-950/50 text-blue-700 dark:text-blue-300";
      case "completed":
        return "bg-green-100 dark:bg-green-950/50 text-green-700 dark:text-green-300";
      case "error":
        return "bg-red-100 dark:bg-red-950/50 text-red-700 dark:text-red-300";
      case "paused":
        return "bg-yellow-100 dark:bg-yellow-950/50 text-yellow-700 dark:text-yellow-300";
      case "failed":
        return "bg-red-100 dark:bg-red-950/50 text-red-700 dark:text-red-300";
      case "pending":
        return "bg-yellow-100 dark:bg-yellow-950/50 text-yellow-700 dark:text-yellow-300";
      default:
        return "bg-gray-200 dark:bg-muted text-gray-700 dark:text-muted-foreground";
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
      className="border-border bg-card hover:bg-muted/50 hover:shadow-primary/3 hover:border-primary/10 group cursor-pointer px-0 py-3 transition-all duration-200 hover:shadow-md"
      onClick={() => {
        router.push(`/chat/${thread.id}`);
      }}
    >
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <CardTitle className="text-foreground line-clamp-2 text-sm leading-tight">
              <InlineMarkdownText>{threadTitle}</InlineMarkdownText>
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
              "text-xs transition-all duration-300 group-hover:scale-105",
              isStatusLoading
                ? "dark:bg-muted dark:text-muted-foreground bg-gray-200 text-gray-600"
                : getStatusColor(displayStatus),
            )}
          >
            <div className="flex items-center gap-1">
              {isStatusLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <div className="transition-transform duration-300 group-hover:rotate-12">
                  {getStatusIcon(displayStatus)}
                </div>
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
                className="text-muted-foreground hover:text-foreground h-5 w-5 p-0 transition-all duration-200 hover:scale-110 hover:bg-orange-100 dark:hover:bg-orange-950"
                onClick={(e) => {
                  e.stopPropagation();
                  window.open(thread.githubIssue!.url, "_blank");
                }}
              >
                <Bug className="h-3 w-3 transition-colors duration-200" />
              </Button>
            )}
            {thread.pullRequest && (
              <Button
                variant="ghost"
                size="sm"
                className={cn(
                  "h-5 w-5 p-0 transition-all duration-200 hover:scale-110 hover:text-gray-300",
                  getPRStatusColor(thread.pullRequest.status),
                  thread.pullRequest.status === "merged" &&
                    "hover:bg-purple-100 dark:hover:bg-purple-950",
                  thread.pullRequest.status === "open" &&
                    "hover:bg-green-100 dark:hover:bg-green-950",
                  thread.pullRequest.status === "closed" &&
                    "hover:bg-red-100 dark:hover:bg-red-950",
                )}
                onClick={(e) => {
                  e.stopPropagation();
                  window.open(thread.pullRequest!.url, "_blank");
                }}
              >
                <GitPullRequest className="h-3 w-3 transition-colors duration-200" />
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
    <Card className="border-border bg-card px-0 py-3">
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
