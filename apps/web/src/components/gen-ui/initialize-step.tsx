"use client";
import {
  Loader2,
  CheckCircle,
  XCircle,
  GitBranch,
  MessageSquare,
  FileText,
  ChevronDown,
} from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
// TODO: Replace with import from shared package when path aliasing is fixed
// Temporary local definition of Step type
export type Step = {
  name: string;
  status: "waiting" | "generating" | "success" | "error" | "skipped";
  error?: string;
};

type InitializeStepProps = {
  status: "loading" | "generating" | "done";
  success?: boolean;
  steps?: Step[];
  reasoningText?: string;
  summaryText?: string;
  collapse?: boolean;
};

export function InitializeStep({
  status,
  success,
  steps,
  reasoningText,
  summaryText,
  collapse: collapseProp,
}: InitializeStepProps) {
  const [showReasoning, setShowReasoning] = useState(false);
  const [showSummary, setShowSummary] = useState(false);
  const [collapsed, setCollapsed] = useState(collapseProp ?? false);

  const stepStatusIcon = {
    waiting: (
      <div className={cn("h-3.5 w-3.5 rounded-full border border-gray-300")} />
    ),
    generating: <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-500" />,
    success: <CheckCircle className="h-3.5 w-3.5 text-green-500" />,
    error: <XCircle className="h-3.5 w-3.5 text-red-500" />,
  };

  const getStatusIcon = () => {
    switch (status) {
      case "loading":
        return (
          <div
            className={cn("h-3.5 w-3.5 rounded-full border border-gray-300")}
          />
        );
      case "generating":
        return <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-500" />;
      case "done":
        return success ? (
          <CheckCircle className="h-3.5 w-3.5 text-green-500" />
        ) : (
          <XCircle className="h-3.5 w-3.5 text-red-500" />
        );
    }
  };

  const getStatusText = () => {
    switch (status) {
      case "loading":
        return "Preparing environment...";
      case "generating":
        return "Initializing environment...";
      case "done":
        return success ? "Environment ready" : "Initialization failed";
    }
  };

  return (
    <div className="overflow-hidden rounded-md border border-gray-200">
      {/* Collapse/Expand Icon */}
      <div className="relative flex items-center border-b border-gray-200 bg-gray-50 p-2">
        <GitBranch className="mr-2 h-3.5 w-3.5 text-gray-500" />
        <span className="flex-1 text-xs font-normal text-gray-800">
          {getStatusText()}
        </span>
        {getStatusIcon()}
        <button
          type="button"
          aria-label={collapsed ? "Expand" : "Collapse"}
          onClick={() => setCollapsed((c) => !c)}
          className={cn(
            "ml-2 rounded p-1 transition-colors hover:bg-gray-100",
            "flex items-center justify-center",
          )}
        >
          <ChevronDown
            className={cn(
              "h-4 w-4 transition-transform",
              collapsed ? "rotate-0" : "rotate-180",
            )}
          />
        </button>
      </div>
      {/* Only render the rest if not collapsed */}
      {!collapsed && (
        <>
          {reasoningText && (
            <div className="border-b border-blue-100 bg-blue-50 p-2">
              <button
                onClick={() => setShowReasoning(!showReasoning)}
                className="flex items-center gap-1 text-xs font-normal text-blue-700 hover:text-blue-800"
              >
                <MessageSquare className="h-3 w-3" />
                {showReasoning ? "Hide reasoning" : "Show reasoning"}
              </button>
              {showReasoning && (
                <p className="mt-1 text-xs font-normal text-blue-800">
                  {reasoningText}
                </p>
              )}
            </div>
          )}

          {steps && (
            <div className="p-2">
              <ul className="space-y-2">
                {steps
                  .filter((step) => step.status !== "skipped")
                  .map((step, index) => (
                    <li
                      key={index}
                      className="flex items-center text-xs"
                    >
                      <span className="mr-2">
                        {stepStatusIcon[
                          step.status as keyof typeof stepStatusIcon
                        ] ?? (
                          <div
                            className={cn(
                              "h-3.5 w-3.5 rounded-full border border-gray-300",
                            )}
                          />
                        )}
                      </span>
                      <span
                        className={cn(
                          "font-normal",
                          step.status === "error"
                            ? "text-red-500"
                            : "text-gray-800",
                        )}
                      >
                        {step.name}
                      </span>
                      {step.error && (
                        <span className="ml-2 text-xs text-red-500">
                          ({step.error})
                        </span>
                      )}
                    </li>
                  ))}
              </ul>
            </div>
          )}

          {summaryText && status === "done" && (
            <div className="border-t border-green-100 bg-green-50 p-2">
              <button
                onClick={() => setShowSummary(!showSummary)}
                className="flex items-center gap-1 text-xs font-normal text-green-700 hover:text-green-800"
              >
                <FileText className="h-3 w-3" />
                {showSummary ? "Hide summary" : "Show summary"}
              </button>
              {showSummary && (
                <p className="mt-1 text-xs font-normal text-green-800">
                  {summaryText}
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
