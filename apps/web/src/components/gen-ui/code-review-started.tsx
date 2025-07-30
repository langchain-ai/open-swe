"use client";

import { FileSearch } from "lucide-react";
import { Badge } from "../ui/badge";
import { Clock } from "lucide-react";
import { cn } from "@/lib/utils";

type CodeReviewStartedProps = {
  status?: "generating" | "done";
};

export function CodeReviewStarted({ status = "done" }: CodeReviewStartedProps) {
  return (
    <div
      className={cn(
        "dark:border-muted-foreground/20 dark:bg-muted/30 rounded-lg border border-blue-200/60 bg-blue-50/30 shadow-sm transition-shadow",
        "shadow-sm hover:shadow-md",
      )}
    >
      {/* Header */}
      <div
        className={cn(
          "dark:bg-muted/40 relative flex items-center bg-blue-50/50 p-3",
          "rounded-lg",
        )}
      >
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-blue-500/90 dark:bg-blue-600">
          <FileSearch className="h-3.5 w-3.5 text-white" />
        </div>

        <div className="ml-3 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-foreground text-sm font-medium">Code review</h3>
            <Badge
              variant="secondary"
              className="border-blue-200/60 bg-blue-100/80 text-blue-700 dark:border-blue-700/40 dark:bg-blue-900/50 dark:text-blue-300"
            >
              <Clock className="h-3 w-3" />
              In progress
            </Badge>
          </div>
          <p className="text-muted-foreground/80 mt-1 text-xs">
            Analyzing code quality
          </p>
        </div>
      </div>
    </div>
  );
}
