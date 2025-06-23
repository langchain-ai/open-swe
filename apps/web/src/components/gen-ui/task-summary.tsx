"use client";

import { useState } from "react";
import {
  CheckCircle,
  Loader2,
  ChevronDown,
  ChevronUp,
  FileText,
  MinusCircle,
} from "lucide-react";

type TaskSummaryProps = {
  status: "loading" | "generating" | "done";
  completed?: boolean;
  summary?: string;
  summaryText?: string;
};

export function TaskSummary({
  status,
  completed,
  summary,
  summaryText,
}: TaskSummaryProps) {
  const [expanded, setExpanded] = useState(false);
  const [showSummary, setShowSummary] = useState(false);

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
        return "Preparing task summary...";
      case "generating":
        return "Generating summary...";
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
        className={`flex items-center border-b bg-gray-50 p-2 dark:bg-gray-800 ${status === "done" && summary ? "cursor-pointer" : ""}`}
        onClick={
          status === "done" && summary
            ? () => setExpanded(!expanded)
            : undefined
        }
      >
        {getStatusIcon()}
        <span className="text-foreground/80 ml-2 flex-1 text-xs font-normal">
          {getStatusText()}
        </span>
        {status === "done" && summary && (
          <button className="text-muted-foreground hover:text-foreground">
            {expanded ? (
              <ChevronUp className="size-3.5" />
            ) : (
              <ChevronDown className="size-3.5" />
            )}
          </button>
        )}
      </div>

      {expanded && summary && status === "done" && (
        <div className="border-border border-t p-2">
          <h3 className="text-muted-foreground mb-1 text-xs font-normal">
            Task Summary
          </h3>
          <p className="text-foreground/80 text-xs font-normal">{summary}</p>
        </div>
      )}

      {summaryText && status === "done" && (
        <div
          className={`border-t p-2 ${
            completed === false
              ? "border-amber-300 bg-amber-100/50 dark:border-amber-800 dark:bg-amber-900/50"
              : "border-green-300 bg-green-100/50 dark:border-green-800 dark:bg-green-900/50"
          }`}
        >
          <button
            onClick={() => setShowSummary(!showSummary)}
            className={`flex cursor-pointer items-center gap-1 text-xs font-normal ${
              completed === false
                ? "text-amber-600 hover:text-amber-700 dark:text-amber-400 dark:hover:text-amber-300"
                : "text-green-600 hover:text-green-700 dark:text-green-400 dark:hover:text-green-300"
            }`}
          >
            <FileText className="h-3 w-3" />
            {showSummary ? "Hide summary" : "Show summary"}
          </button>
          {showSummary && (
            <p
              className={`mt-1 text-xs font-normal ${
                completed === false
                  ? "text-amber-700 dark:text-amber-300"
                  : "text-green-700 dark:text-green-300"
              }`}
            >
              {summaryText}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
