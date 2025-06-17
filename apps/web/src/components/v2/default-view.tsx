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
  Camera,
  Upload,
  FileText,
  CheckCircle,
  XCircle,
  Loader2,
  GitBranch,
  GitPullRequest,
  Bug,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { ThreadDisplayInfo } from "./types";
import { TerminalInput } from "./terminal-input";
import { useQueryState } from "nuqs";

interface DefaultViewProps {
  threads: ThreadDisplayInfo[];
  onThreadSelect: (thread: ThreadDisplayInfo) => void;
}

export function DefaultView({ threads, onThreadSelect }: DefaultViewProps) {
  const router = useRouter();
  const [selectedRepo] = useQueryState("repo");
  const [selectedBranch] = useQueryState("branch");

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

  const handleSubmit = (message: string) => {
    alert(
      `Creating new thread with: ${message} to ${selectedRepo}:${selectedBranch}`,
    );
  };

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
          <Card className="border-gray-800 bg-gray-950 py-0">
            <CardContent className="p-4">
              <div className="space-y-3">
                <TerminalInput
                  onSend={handleSubmit}
                  placeholder="Describe your coding task or ask a question..."
                />
                <div className="flex items-center gap-1">
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 border-gray-700 bg-gray-900 text-xs text-gray-400 hover:bg-gray-800 hover:text-gray-300"
                    onClick={() => alert("Not implemented")}
                  >
                    <Upload className="mr-1 h-3 w-3" />
                    Upload File
                  </Button>
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
                  className="cursor-pointer border-gray-800 bg-gray-950 transition-shadow hover:bg-gray-900 hover:shadow-lg"
                  onClick={() => onThreadSelect(thread)}
                >
                  <CardHeader className="p-3 pb-2">
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
                  <CardContent className="p-3 pt-0">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-gray-600">
                          {thread.taskCount} tasks
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
