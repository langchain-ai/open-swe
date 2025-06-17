"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  CheckCircle,
  XCircle,
  Loader2,
  GitBranch,
  Layers3,
  Plus,
  Bug,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { getThreadTitle } from "@/lib/thread";
import { getActivePlanItems } from "@open-swe/shared/open-swe/tasks";
import { ThreadDisplayInfo } from "./types";

interface ThreadSwitcherProps {
  currentThread: ThreadDisplayInfo;
  allThreads: ThreadDisplayInfo[];
  onThreadSelect: (thread: ThreadDisplayInfo) => void;
  onNewChat: () => void;
}

export function ThreadSwitcher({
  currentThread,
  allThreads,
  onThreadSelect,
  onNewChat,
}: ThreadSwitcherProps) {
  const [open, setOpen] = useState(false);
  const router = useRouter();

  const getStatusIcon = (status: ThreadDisplayInfo["status"]) => {
    switch (status) {
      case "running":
        return <Loader2 className="h-3 w-3 animate-spin text-blue-400" />;
      case "completed":
        return <CheckCircle className="h-3 w-3 text-green-400" />;
      case "failed":
        return <XCircle className="h-3 w-3 text-red-400" />;
      default:
        return <div className="h-3 w-3 rounded-full bg-gray-700" />;
    }
  };

  const getStatusColor = (status: ThreadDisplayInfo["status"]) => {
    switch (status) {
      case "running":
        return "bg-blue-950 text-blue-400";
      case "completed":
        return "bg-green-950 text-green-400";
      case "failed":
        return "bg-red-950 text-red-400";
      default:
        return "bg-gray-800 text-gray-400";
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

  const otherThreads = allThreads.filter((t) => t.id !== currentThread.id);
  const runningCount = otherThreads.filter(
    (t) => t.status === "running",
  ).length;

  return (
    <Sheet
      open={open}
      onOpenChange={setOpen}
    >
      <SheetTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-7 gap-1 border-gray-700 bg-gray-900 text-xs text-gray-400 hover:bg-gray-800 hover:text-gray-300"
        >
          <Layers3 className="h-3 w-3" />
          <span className="hidden sm:inline">Switch Thread</span>
          {runningCount > 0 && (
            <Badge
              variant="secondary"
              className="h-4 bg-blue-950 px-1 text-xs text-blue-400"
            >
              {runningCount}
            </Badge>
          )}
        </Button>
      </SheetTrigger>
      <SheetContent
        side="right"
        className="w-80 border-gray-800 bg-gray-950 sm:w-96"
      >
        <SheetHeader className="pb-4">
          <SheetTitle className="text-base text-gray-300">
            All Threads
          </SheetTitle>
        </SheetHeader>

        <div className="space-y-3">
          {/* New Chat Button */}
          <Button
            onClick={() => {
              router.push("/chat");
              setOpen(false);
            }}
            className="h-8 w-full justify-start gap-2 border-gray-700 bg-gray-900 text-xs text-gray-300 hover:bg-gray-800"
            variant="outline"
          >
            <Plus className="h-3 w-3" />
            Start New Chat
          </Button>

          {/* Current Thread */}
          <div className="space-y-2">
            <h3 className="text-xs font-medium tracking-wide text-gray-500 uppercase">
              Current Thread
            </h3>
            <div className="rounded-lg border-2 border-blue-800 bg-blue-950 p-3">
              <div className="flex items-start gap-2">
                {getStatusIcon(currentThread.status)}
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs font-medium text-gray-300">
                    {currentThread.title}
                  </div>
                  <div className="mt-1 flex items-center gap-1">
                    <GitBranch className="h-2 w-2 text-gray-600" />
                    <span className="truncate text-xs text-gray-500">
                      {currentThread.repository}
                    </span>
                  </div>
                  <div className="mt-2 flex items-center justify-between">
                    <Badge
                      variant="secondary"
                      className={`${getStatusColor(currentThread.status)} text-xs`}
                    >
                      {currentThread.status}
                    </Badge>
                    <div className="flex items-center gap-1">
                      {currentThread.githubIssue && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-5 w-5 p-0 text-gray-500 hover:text-gray-300"
                          onClick={(e) => {
                            e.stopPropagation();
                            // TODO: Open issue in GitHub
                            alert("Open issue in github not implemented.");
                          }}
                        >
                          <Bug className="h-3 w-3" />
                        </Button>
                      )}
                      {/* TODO: Add PR info to state, then hook this up. */}
                      {/* {currentThread.pullRequest && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className={`h-5 w-5 p-0 hover:text-gray-300 ${getPRStatusColor(currentThread.pullRequest.status)}`}
                          onClick={(e) => {
                            e.stopPropagation()
                            window.open(currentThread.pullRequest!.url, "_blank")
                          }}
                        >
                          <GitPullRequest className="h-3 w-3" />
                        </Button>
                      )} */}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Other Threads */}
          {otherThreads.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-medium tracking-wide text-gray-500 uppercase">
                Other Threads
              </h3>
              <ScrollArea className="h-96">
                <div className="space-y-1">
                  {otherThreads.map((thread) => (
                    <Button
                      key={thread.id}
                      variant="ghost"
                      className="h-auto w-full justify-start p-3 text-left text-gray-400 hover:bg-gray-800"
                      onClick={() => {
                        router.push(`/chat/${thread.id}`);
                        setOpen(false);
                      }}
                    >
                      <div className="flex w-full items-start gap-2">
                        {getStatusIcon(thread.status)}
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-xs font-medium text-gray-300">
                            {thread.title}
                          </div>
                          <div className="mt-1 flex items-center gap-1">
                            <GitBranch className="h-2 w-2 text-gray-600" />
                            <span className="truncate text-xs text-gray-500">
                              {thread.repository}
                            </span>
                          </div>
                          <div className="mt-1 flex items-center justify-between">
                            {thread.taskCount && (
                              <div className="flex items-center gap-2">
                                <span className="text-xs text-gray-600">
                                  {thread.taskCount} tasks
                                </span>
                                <Badge
                                  variant="secondary"
                                  className={`${getStatusColor(thread.status)} text-xs`}
                                >
                                  {thread.status}
                                </Badge>
                              </div>
                            )}

                            <div className="flex items-center gap-1">
                              {thread.githubIssue && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-4 w-4 p-0 text-gray-600 hover:text-gray-400"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    // TODO: Open issue in GitHub
                                    alert(
                                      "Open issue in github not implemented.",
                                    );
                                  }}
                                >
                                  <Bug className="h-2 w-2" />
                                </Button>
                              )}
                              {/* TODO: Add PR info to state, then hook this up. */}
                              {/* {thread.pullRequest && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className={`h-4 w-4 p-0 hover:text-gray-400 ${getPRStatusColor(thread.pullRequest.status)}`}
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    window.open(thread.pullRequest!.url, "_blank")
                                  }}
                                >
                                  <GitPullRequest className="h-2 w-2" />
                                </Button>
                              )} */}
                            </div>
                          </div>
                        </div>
                      </div>
                    </Button>
                  ))}
                </div>
              </ScrollArea>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
