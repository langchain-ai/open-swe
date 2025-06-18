import {
  Bug,
  CheckCircle,
  GitBranch,
  GitPullRequest,
  Loader2,
  XCircle,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { ThreadDisplayInfo } from "./types";
import { useRouter } from "next/navigation";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";

export function ThreadCard({ thread }: { thread: ThreadDisplayInfo }) {
  const router = useRouter();

  const getStatusColor = (status: ThreadDisplayInfo["status"]) => {
    switch (status) {
      case "running":
        return "dark:bg-blue-950 bg-blue-100 dark:text-blue-400 text-blue-700";
      case "completed":
        return "dark:bg-green-950 bg-green-100 dark:text-green-400 text-green-700";
      case "failed":
        return "dark:bg-red-950 bg-red-100 dark:text-red-400 text-red-700";
      case "pending":
        return "dark:bg-yellow-950 bg-yellow-100 dark:text-yellow-400 text-yellow-700";
      default:
        return "dark:bg-gray-800 bg-gray-200 dark:text-gray-400 text-gray-700";
    }
  };

  const getStatusIcon = (status: ThreadDisplayInfo["status"]) => {
    switch (status) {
      case "running":
        return <Loader2 className="h-4 w-4 animate-spin" />;
      case "completed":
        return <CheckCircle className="h-4 w-4" />;
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
            className={`${getStatusColor(thread.status)} text-xs`}
          >
            <div className="flex items-center gap-1">
              {getStatusIcon(thread.status)}
              <span className="capitalize">{thread.status}</span>
            </div>
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground text-xs">
              {thread.taskCount === 0
                ? "No tasks"
                : `${thread.taskCount} tasks`}
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
                className={`h-5 w-5 p-0 hover:text-gray-300 ${getPRStatusColor(thread.pullRequest.status)}`}
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
