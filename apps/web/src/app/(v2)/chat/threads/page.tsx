"use client";

import type React from "react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  ArrowLeft,
  Search,
  Filter,
  CheckCircle,
  XCircle,
  Loader2,
  Clock,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { ThreadDisplayInfo, threadToDisplayInfo } from "@/components/v2/types";
import { useThreads } from "@/hooks/useThreads";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { ThreadCard } from "@/components/v2/thread-card";
import { ThemeToggle } from "@/components/theme-toggle";

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
      case "pending":
        return <Clock className="h-4 w-4" />;
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
    <div className="bg-background flex h-screen flex-col">
      {/* Header */}
      <div className="border-border bg-card border-b px-4 py-3">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:bg-muted hover:text-foreground h-6 w-6 p-0"
            onClick={() => router.push("/chat")}
          >
            <ArrowLeft className="h-3 w-3" />
          </Button>
          <div className="flex items-center gap-2">
            <div className="h-2 w-2 rounded-full bg-green-500"></div>
            <span className="text-muted-foreground font-mono text-sm">
              All Threads
            </span>
          </div>
          <div className="ml-auto flex items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground text-xs">
                {filteredThreads.length} threads
              </span>
            </div>
            <ThemeToggle />
          </div>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="border-border bg-muted/50 border-b px-4 py-3 dark:bg-gray-950">
        <div className="flex items-center gap-3">
          <div className="relative max-w-md flex-1">
            <Search className="text-muted-foreground absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 transform" />
            <Input
              placeholder="Search threads..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="border-border bg-background text-foreground placeholder:text-muted-foreground pl-10 dark:bg-gray-900"
            />
          </div>
          <div className="flex items-center gap-1">
            <Filter className="text-muted-foreground h-4 w-4" />
            <span className="text-muted-foreground mr-2 text-xs">Filter:</span>
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
                    ? "bg-muted text-foreground dark:bg-gray-700"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                }`}
                onClick={() => setStatusFilter(status)}
              >
                {status === "all"
                  ? "All"
                  : status.charAt(0).toUpperCase() + status.slice(1)}
                <Badge
                  variant="secondary"
                  className="bg-muted/70 text-muted-foreground ml-1 text-xs dark:bg-gray-800"
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
                      <h2 className="text-foreground text-base font-semibold capitalize">
                        {status} Threads
                      </h2>
                      <Badge
                        variant="secondary"
                        className="bg-muted/70 text-muted-foreground text-xs dark:bg-gray-800"
                      >
                        {threads.length}
                      </Badge>
                    </div>
                    <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                      {threads.map((thread) => (
                        <ThreadCard
                          key={thread.id}
                          thread={thread}
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
                />
              ))}
            </div>
          )}

          {filteredThreads.length === 0 && (
            <div className="py-12 text-center">
              <div className="text-muted-foreground mb-2">No threads found</div>
              <div className="text-muted-foreground/70 text-xs">
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
