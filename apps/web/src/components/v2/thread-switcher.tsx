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
import { ThreadDisplayInfo } from "./types";
import { ThreadCard } from "./thread-card";

interface ThreadSwitcherProps {
  currentThread: ThreadDisplayInfo;
  allThreads: ThreadDisplayInfo[];
}

export function ThreadSwitcher({
  currentThread,
  allThreads,
}: ThreadSwitcherProps) {
  const [open, setOpen] = useState(false);
  const router = useRouter();

  const getStatusIcon = (status: ThreadDisplayInfo["status"]) => {
    switch (status) {
      case "running":
        return (
          <Loader2 className="h-3 w-3 animate-spin text-blue-600 dark:text-blue-400" />
        );
      case "completed":
        return (
          <CheckCircle className="h-3 w-3 text-green-600 dark:text-green-400" />
        );
      case "failed":
        return <XCircle className="h-3 w-3 text-red-600 dark:text-red-400" />;
      default:
        return (
          <div className="bg-muted-foreground/50 h-3 w-3 rounded-full dark:bg-gray-700" />
        );
    }
  };

  const getStatusColor = (status: ThreadDisplayInfo["status"]) => {
    switch (status) {
      case "running":
        return "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-400";
      case "completed":
        return "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-400";
      case "failed":
        return "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400";
      default:
        return "bg-muted text-muted-foreground dark:bg-gray-800 dark:text-gray-400";
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
          className="border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground h-7 gap-1 text-xs dark:border-gray-700 dark:bg-gray-900 dark:hover:bg-gray-800 dark:hover:text-gray-300"
        >
          <Layers3 className="h-3 w-3" />
          <span className="hidden sm:inline">Switch Thread</span>
          {runningCount > 0 && (
            <Badge
              variant="secondary"
              className="h-4 bg-blue-100 px-1 text-xs text-blue-700 dark:bg-blue-950 dark:text-blue-400"
            >
              {runningCount}
            </Badge>
          )}
        </Button>
      </SheetTrigger>
      <SheetContent
        side="right"
        className="border-border bg-background w-80 sm:w-96 dark:border-gray-800 dark:bg-gray-950"
      >
        <SheetHeader className="pb-4">
          <SheetTitle className="text-foreground text-base">
            All Threads
          </SheetTitle>
        </SheetHeader>

        <div className="mx-2 h-full space-y-3">
          {/* New Chat Button */}
          <Button
            onClick={() => {
              router.push("/chat");
              setOpen(false);
            }}
            className="border-border bg-card text-foreground hover:bg-muted h-8 w-full justify-start gap-2 text-xs dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:bg-gray-800"
            variant="outline"
          >
            <Plus className="h-3 w-3" />
            Start New Chat
          </Button>

          {/* Current Thread */}
          <div className="space-y-2">
            <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase dark:text-gray-500">
              Current Thread
            </h3>
            <ThreadCard thread={currentThread} />
          </div>

          {/* Other Threads */}
          {otherThreads.length > 0 && (
            <div className="h-full space-y-2">
              <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase dark:text-gray-500">
                Other Threads
              </h3>
              <ScrollArea className="h-full">
                <div className="space-y-1">
                  {otherThreads.map((thread) => (
                    <ThreadCard
                      thread={thread}
                      key={thread.id}
                    />
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
