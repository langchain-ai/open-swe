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
        return "bg-blue-950 text-blue-400";
      case "completed":
        return "bg-green-950 text-green-400";
      case "failed":
        return "bg-red-950 text-red-400";
      case "pending":
        return "bg-yellow-950 text-yellow-400";
      default:
        return "bg-gray-800 text-gray-400";
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
        return "text-purple-400";
      case "open":
        return "text-green-400";
      case "draft":
        return "text-gray-400";
      case "closed":
        return "text-red-400";
      default:
        return "text-gray-400";
    }
  };

  if (!apiUrl || !assistantId) {
    return <div>Missing API URL or Assistant ID</div>;
  }

  return (
    <div className="flex flex-1 flex-col">
      {/* Header */}
      <div className="border-b border-gray-900 bg-black px-4 py-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="h-2 w-2 rounded-full bg-green-500"></div>
            <span className="font-mono text-sm text-gray-400">Open SWE</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-600">ready</span>
            <div className="h-1 w-1 rounded-full bg-gray-600"></div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-4xl space-y-6 p-4">
          {/* Terminal Chat Input */}
          <Card
            className={cn(
              "border-gray-800 bg-gray-950 py-0",
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
                          className="flex cursor-pointer items-center justify-center rounded-full bg-inherit text-gray-500 hover:text-gray-300"
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
              <h2 className="text-base font-semibold text-gray-300">
                Recent & Running Threads
              </h2>
              <Button
                variant="outline"
                size="sm"
                className="h-7 border-gray-700 bg-gray-900 text-xs text-gray-400 hover:bg-gray-800 hover:text-gray-300"
                onClick={() => router.push("/chat/threads")}
              >
                View All
              </Button>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              {threads.slice(0, 4).map((thread) => (
                <Card
                  key={thread.id}
                  className="cursor-pointer border-gray-800 bg-gray-950 px-0 py-3 transition-shadow hover:bg-gray-900 hover:shadow-lg"
                  onClick={() => {
                    router.push(`/chat/${thread.id}`);
                  }}
                >
                  <CardHeader>
                    <div className="flex items-start justify-between">
                      <div className="min-w-0 flex-1">
                        <CardTitle className="truncate text-sm font-medium text-gray-300">
                          {thread.title}
                        </CardTitle>
                        <div className="mt-1 flex items-center gap-1">
                          <GitBranch className="h-2 w-2 text-gray-600" />
                          <span className="truncate text-xs text-gray-500">
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
                        <span className="text-xs text-gray-600">
                          {thread.taskCount === 0
                            ? "No tasks"
                            : `${thread.taskCount} tasks`}
                        </span>
                        <span className="text-xs text-gray-600">•</span>
                        <span className="text-xs text-gray-600">
                          {thread.lastActivity}
                        </span>
                      </div>
                      <div className="flex items-center gap-1">
                        {thread.githubIssue && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-5 w-5 p-0 text-gray-500 hover:text-gray-300"
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
            <h2 className="mb-3 text-base font-semibold text-gray-300">
              Quick Actions
            </h2>
            <div className="grid gap-3 md:grid-cols-3">
              <Card className="cursor-pointer border-gray-800 bg-gray-950 py-3 transition-shadow hover:bg-gray-900 hover:shadow-lg">
                <CardHeader className="px-3">
                  <CardTitle className="text-sm text-gray-300">
                    Debug Code
                  </CardTitle>
                  <CardDescription className="text-xs text-gray-500">
                    Find and fix issues in your codebase
                  </CardDescription>
                </CardHeader>
              </Card>
              <Card className="cursor-pointer border-gray-800 bg-gray-950 py-3 transition-shadow hover:bg-gray-900 hover:shadow-lg">
                <CardHeader className="px-3">
                  <CardTitle className="text-sm text-gray-300">
                    Add Feature
                  </CardTitle>
                  <CardDescription className="text-xs text-gray-500">
                    Implement new functionality
                  </CardDescription>
                </CardHeader>
              </Card>
              <Card className="cursor-pointer border-gray-800 bg-gray-950 py-3 transition-shadow hover:bg-gray-900 hover:shadow-lg">
                <CardHeader className="px-3">
                  <CardTitle className="text-sm text-gray-300">
                    Refactor Code
                  </CardTitle>
                  <CardDescription className="text-xs text-gray-500">
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
