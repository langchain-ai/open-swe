"use client";

import type React from "react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  ArrowLeft,
  Search,
  Filter,
  CheckCircle,
  XCircle,
  Loader2,
  GitBranch,
  GitPullRequest,
  Bug,
  Calendar,
  Clock,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { ThreadDisplayInfo, threadToDisplayInfo } from "@/components/v2/types";
import { useThreads } from "@/hooks/useThreads";
import { GraphState } from "@open-swe/shared/open-swe/types";

type FilterStatus = "all" | "running" | "completed" | "failed" | "pending";

export default function AllThreadsPage() {
  const router = useRouter();
  const { threads } = useThreads<GraphState>();
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<FilterStatus>("all");

  // Convert Thread objects to ThreadDisplayInfo for UI
  const displayThreads: ThreadDisplayInfo[] =
    threads?.map(threadToDisplayInfo) ?? [];

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
      case "pending":
        return <Clock className="h-4 w-4" />;
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

  // Filter and search threads
  const filteredThreads = displayThreads.filter((thread) => {
    const matchesSearch =
      thread.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      thread.repository.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus =
      statusFilter === "all" || thread.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  // Group threads by status
  const groupedThreads = {
    running: filteredThreads.filter((t) => t.status === "running"),
    completed: filteredThreads.filter((t) => t.status === "completed"),
    failed: filteredThreads.filter((t) => t.status === "failed"),
    pending: filteredThreads.filter((t) => t.status === "pending"),
  };

  const statusCounts = {
    all: displayThreads.length,
    running: displayThreads.filter((t) => t.status === "running").length,
    completed: displayThreads.filter((t) => t.status === "completed").length,
    failed: displayThreads.filter((t) => t.status === "failed").length,
    pending: displayThreads.filter((t) => t.status === "pending").length,
  };

  const handleThreadClick = (thread: ThreadDisplayInfo) => {
    router.push(`/chat/${thread.id}`);
  };

  return (
    <div className="flex h-screen flex-col bg-black">
      {/* Header */}
      <div className="border-b border-gray-900 bg-black px-4 py-3">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0 text-gray-600 hover:bg-gray-900 hover:text-gray-400"
            onClick={() => router.push("/chat")}
          >
            <ArrowLeft className="h-3 w-3" />
          </Button>
          <div className="flex items-center gap-2">
            <div className="h-2 w-2 rounded-full bg-green-500"></div>
            <span className="font-mono text-sm text-gray-400">All Threads</span>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <span className="text-xs text-gray-600">
              {filteredThreads.length} threads
            </span>
          </div>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="border-b border-gray-900 bg-gray-950 px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="relative max-w-md flex-1">
            <Search className="absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 transform text-gray-500" />
            <Input
              placeholder="Search threads..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="border-gray-700 bg-gray-900 pl-10 text-gray-300 placeholder:text-gray-600"
            />
          </div>
          <div className="flex items-center gap-1">
            <Filter className="h-4 w-4 text-gray-500" />
            <span className="mr-2 text-xs text-gray-500">Filter:</span>
            {(
              [
                "all",
                "running",
                "completed",
                "failed",
                "pending",
              ] as FilterStatus[]
            ).map((status) => (
              <Button
                key={status}
                variant={statusFilter === status ? "secondary" : "ghost"}
                size="sm"
                className={`h-7 text-xs ${
                  statusFilter === status
                    ? "bg-gray-700 text-gray-200"
                    : "text-gray-500 hover:bg-gray-800 hover:text-gray-300"
                }`}
                onClick={() => setStatusFilter(status)}
              >
                {status === "all"
                  ? "All"
                  : status.charAt(0).toUpperCase() + status.slice(1)}
                <Badge
                  variant="secondary"
                  className="ml-1 bg-gray-800 text-xs text-gray-400"
                >
                  {statusCounts[status]}
                </Badge>
              </Button>
            ))}
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-6xl p-4">
          {statusFilter === "all" ? (
            // Show grouped view when "all" is selected
            <div className="space-y-6">
              {Object.entries(groupedThreads).map(([status, threads]) => {
                if (threads.length === 0) return null;
                return (
                  <div key={status}>
                    <div className="mb-3 flex items-center gap-2">
                      <h2 className="text-base font-semibold text-gray-300 capitalize">
                        {status} Threads
                      </h2>
                      <Badge
                        variant="secondary"
                        className="bg-gray-800 text-xs text-gray-400"
                      >
                        {threads.length}
                      </Badge>
                    </div>
                    <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                      {threads.map((thread) => (
                        <ThreadCard
                          key={thread.id}
                          thread={thread}
                          onClick={() => handleThreadClick(thread)}
                          getStatusColor={getStatusColor}
                          getStatusIcon={getStatusIcon}
                          getPRStatusColor={getPRStatusColor}
                        />
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            // Show flat list when specific status is selected
            <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
              {filteredThreads.map((thread) => (
                <ThreadCard
                  key={thread.id}
                  thread={thread}
                  onClick={() => handleThreadClick(thread)}
                  getStatusColor={getStatusColor}
                  getStatusIcon={getStatusIcon}
                  getPRStatusColor={getPRStatusColor}
                />
              ))}
            </div>
          )}

          {filteredThreads.length === 0 && (
            <div className="py-12 text-center">
              <div className="mb-2 text-gray-500">No threads found</div>
              <div className="text-xs text-gray-600">
                {searchQuery
                  ? "Try adjusting your search query"
                  : "No threads match the selected filter"}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

interface ThreadCardProps {
  thread: ThreadDisplayInfo;
  onClick: () => void;
  getStatusColor: (status: ThreadDisplayInfo["status"]) => string;
  getStatusIcon: (status: ThreadDisplayInfo["status"]) => React.ReactNode;
  getPRStatusColor: (status: string) => string;
}

function ThreadCard({
  thread,
  onClick,
  getStatusColor,
  getStatusIcon,
  getPRStatusColor,
}: ThreadCardProps) {
  return (
    <Card
      className="cursor-pointer border-gray-800 bg-gray-950 transition-shadow hover:bg-gray-900 hover:shadow-lg"
      onClick={onClick}
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
            <div className="flex items-center gap-1">
              <Calendar className="h-2 w-2 text-gray-600" />
              <span className="text-xs text-gray-600">
                {thread.lastActivity}
              </span>
            </div>
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
  );
}
