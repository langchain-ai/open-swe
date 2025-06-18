"use client";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  CheckCircle,
  XCircle,
  Loader2,
  GitBranch,
  GitPullRequest,
  Bug,
  FilePlus2,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { ThreadDisplayInfo } from "./types";
import { TerminalInput } from "./terminal-input";
import { useFileUpload } from "@/hooks/useFileUpload";
import { cn } from "@/lib/utils";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";
import { Label } from "../ui/label";
import { ContentBlocksPreview } from "../thread/ContentBlocksPreview";
import { TooltipIconButton } from "../ui/tooltip-icon-button";
import { ThemeToggle } from "../theme-toggle";

interface DefaultViewProps {
  threads: ThreadDisplayInfo[];
}

export function DefaultView({ threads }: DefaultViewProps) {
  const router = useRouter();
  const apiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL ?? "";
  const assistantId: string | undefined =
    process.env.NEXT_PUBLIC_MANAGER_ASSISTANT_ID ?? "";
  const {
    contentBlocks,
    setContentBlocks,
    handleFileUpload,
    dropRef,
    removeBlock,
    dragOver,
    handlePaste,
  } = useFileUpload();

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

  if (!apiUrl || !assistantId) {
    return <div>Missing API URL or Assistant ID</div>;
  }

  return (
    <div className="flex flex-1 flex-col">
      {/* Header */}
      <div className="border-border bg-card border-b px-4 py-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="h-2 w-2 rounded-full bg-green-500"></div>
            <span className="text-muted-foreground font-mono text-sm">
              Open SWE
            </span>
          </div>
          <div className="flex items-center gap-4">
            <ThemeToggle />
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground text-xs">ready</span>
              <div className="bg-muted h-1 w-1 rounded-full"></div>
            </div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-4xl space-y-6 p-4">
          {/* Terminal Chat Input */}
          <Card
            className={cn(
              "border-border bg-card py-0 dark:bg-gray-950",
              dragOver
                ? "border-primary border-2 border-dotted"
                : "border border-solid",
            )}
            ref={dropRef}
          >
            <CardContent className="p-4">
              <ContentBlocksPreview
                blocks={contentBlocks}
                onRemove={removeBlock}
              />
              <input
                id="file-input"
                type="file"
                onChange={handleFileUpload}
                multiple
                accept="image/jpeg,image/png,image/gif,image/webp,application/pdf"
                className="hidden"
              />
              <div className="space-y-3">
                <TerminalInput
                  placeholder="Describe your coding task or ask a question..."
                  apiUrl={apiUrl}
                  assistantId={assistantId}
                  contentBlocks={contentBlocks}
                  setContentBlocks={setContentBlocks}
                  onPaste={handlePaste}
                />
                <div className="flex items-center gap-1">
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger>
                        <Label
                          htmlFor="file-input"
                          className="text-muted-foreground hover:text-foreground flex cursor-pointer items-center justify-center rounded-full bg-inherit"
                        >
                          <FilePlus2 className="size-4" />
                        </Label>
                      </TooltipTrigger>
                      <TooltipContent>Attach files</TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Recent & Running Threads */}
          <div>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-foreground text-base font-semibold">
                Recent & Running Threads
              </h2>
              <Button
                variant="outline"
                size="sm"
                className="border-border text-muted-foreground hover:text-foreground h-7 text-xs"
                onClick={() => router.push("/chat/threads")}
              >
                View All
              </Button>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              {threads.slice(0, 4).map((thread) => (
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
              ))}
            </div>
          </div>

          {/* Quick Actions */}
          <div>
            <h2 className="text-foreground mb-3 text-base font-semibold">
              Quick Actions
            </h2>
            <div className="grid gap-3 md:grid-cols-3">
              <Card className="border-border bg-card hover:bg-muted cursor-pointer py-3 transition-shadow hover:shadow-lg dark:bg-gray-950">
                <CardHeader className="px-3">
                  <CardTitle className="text-foreground text-sm">
                    Debug Code
                  </CardTitle>
                  <CardDescription className="text-muted-foreground text-xs">
                    Find and fix issues in your codebase
                  </CardDescription>
                </CardHeader>
              </Card>
              <Card className="border-border bg-card hover:bg-muted cursor-pointer py-3 transition-shadow hover:shadow-lg dark:bg-gray-950">
                <CardHeader className="px-3">
                  <CardTitle className="text-foreground text-sm">
                    Add Feature
                  </CardTitle>
                  <CardDescription className="text-muted-foreground text-xs">
                    Implement new functionality
                  </CardDescription>
                </CardHeader>
              </Card>
              <Card className="border-border bg-card hover:bg-muted cursor-pointer py-3 transition-shadow hover:shadow-lg dark:bg-gray-950">
                <CardHeader className="px-3">
                  <CardTitle className="text-foreground text-sm">
                    Refactor Code
                  </CardTitle>
                  <CardDescription className="text-muted-foreground text-xs">
                    Improve code structure and performance
                  </CardDescription>
                </CardHeader>
              </Card>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
