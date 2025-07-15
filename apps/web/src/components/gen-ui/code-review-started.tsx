"use client";

import { Sparkles } from "lucide-react";
import { Badge } from "../ui/badge";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

type CodeReviewStartedProps = {
  status?: "generating" | "done";
};

export function CodeReviewStarted({ status = "done" }: CodeReviewStartedProps) {
  return (
    <div
      className={cn(
        "group via-background to-background dark:via-background dark:to-background rounded-xl border bg-gradient-to-br from-blue-50/50 transition-shadow dark:from-blue-950/20",
        "shadow-sm hover:shadow-md",
      )}
    >
      {/* Header */}
      <div
        className={cn(
          "relative flex items-center bg-gradient-to-r from-blue-50 to-blue-50/50 p-4 backdrop-blur-sm dark:from-blue-950/30 dark:to-blue-950/10",
          "rounded-xl",
        )}
      >
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-500 shadow-md dark:bg-blue-600">
          <Sparkles className="h-4 w-4 text-white" />
        </div>

        <div className="ml-3 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-foreground text-sm font-semibold">
              Code review started
            </h3>
            <Badge
              variant="secondary"
              className="border-blue-200 bg-blue-100 text-blue-700 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300"
            >
              <Check className="h-3 w-3" />
              In Progress
            </Badge>
          </div>
          <p className="text-muted-foreground mt-1 text-xs">
            Analyzing code for best practices and potential improvements
          </p>
        </div>
      </div>
    </div>
  );
}
