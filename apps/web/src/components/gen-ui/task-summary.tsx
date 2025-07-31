"use client";

import { useState } from "react";
import { CheckCircle, Loader2, FileText, MinusCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { BasicMarkdownText } from "../thread/markdown-text";

type TaskSummaryProps = {
  status: "loading" | "generating" | "done";
  completed?: boolean;
  summaryText?: string;
};

export function TaskSummary({
  status,
  completed,
  summaryText,
}: TaskSummaryProps) {
  const [expanded, setExpanded] = useState(false);
  const [showSummary, setShowSummary] = useState(true);

  const getStatusIcon = () => {
    switch (status) {
      case "loading":
        return <div className="border-border size-3.5 rounded-full border" />;
      case "generating":
        return (
          <Loader2 className="text-muted-foreground size-3.5 animate-spin" />
        );
      case "done":
        if (completed === false) {
          return <MinusCircle className="size-3.5 text-amber-500" />;
        }
        return <CheckCircle className="size-3.5 text-green-500" />;
    }
  };

  const getStatusText = () => {
    switch (status) {
      case "loading":
        return "Preparing action reflection...";
      case "generating":
        return "Generating action reflection...";
      case "done":
        if (completed === false) {
          return "Task not completed";
        }
        return "Task completed";
    }
  };

  return (
    <div className="border-border overflow-hidden rounded-md border">
      <div
        className={"flex items-center border-b bg-gray-50 p-2 dark:bg-gray-800"}
        onClick={
          status === "done" && summaryText
            ? () => setExpanded(!expanded)
            : undefined
        }
      >
        {getStatusIcon()}
        <span className="text-foreground/80 ml-2 flex-1 text-xs font-normal">
          {getStatusText()}
        </span>
      </div>

      {summaryText && (
        <div
          className={cn(
            "border-t p-2",
            completed === false
              ? "border-amber-300 bg-amber-100/50 dark:border-amber-800 dark:bg-amber-900/50"
              : "border-green-300 bg-green-100/50 dark:border-green-800 dark:bg-green-900/50",
          )}
        >
          <button
            onClick={() => setShowSummary(!showSummary)}
            className={cn(
              "flex cursor-pointer items-center gap-1 text-xs font-normal",
              completed === false
                ? "text-amber-600 hover:text-amber-700 dark:text-amber-400 dark:hover:text-amber-300"
                : "text-green-600 hover:text-green-700 dark:text-green-400 dark:hover:text-green-300",
            )}
          >
            <FileText className="h-3 w-3" />
            {showSummary ? "Hide reflection" : "Show reflection"}
          </button>
          {showSummary && (
            <BasicMarkdownText
              className={cn(
                "mt-1 text-xs",
                completed === false
                  ? "text-amber-700 dark:text-amber-300"
                  : "text-green-700 dark:text-green-300",
              )}
            >
              {summaryText}
            </BasicMarkdownText>
          )}
        </div>
      )}
    </div>
  );
}
