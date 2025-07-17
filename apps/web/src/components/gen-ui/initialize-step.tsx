"use client";

import {
  Loader2,
  CheckCircle,
  XCircle,
  GitBranch,
  ChevronDown,
} from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { Step } from "@open-swe/shared/open-swe/custom-node-events";

type InitializeStepProps = {
  status: "loading" | "generating" | "done";
  success?: boolean;
  steps?: Step[];
};

export function InitializeStep({
  status,
  success,
  steps,
}: InitializeStepProps) {
  const [collapsed, setCollapsed] = useState(false);

  const stepStatusIcon = {
    waiting: (
      <div
        className={cn(
          "h-3.5 w-3.5 rounded-full border border-gray-300 dark:border-gray-600",
        )}
      />
    ),
    generating: (
      <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-500 dark:text-gray-400" />
    ),
    success: <CheckCircle className="h-3.5 w-3.5 text-green-500" />,
    error: <XCircle className="h-3.5 w-3.5 text-red-500" />,
  };

  const getStatusIcon = () => {
    switch (status) {
      case "loading":
        return (
          <div
            className={cn(
              "h-3.5 w-3.5 rounded-full border border-gray-300 dark:border-gray-600",
            )}
          />
        );
      case "generating":
        return (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-500 dark:text-gray-400" />
        );
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
    <div className="overflow-hidden rounded-md border border-gray-200 dark:border-gray-700">
      {/* Collapse/Expand Icon */}
      <div className="relative flex items-center border-b border-gray-200 bg-gray-50 p-2 dark:border-gray-700 dark:bg-gray-800">
        <GitBranch className="mr-2 h-3.5 w-3.5 text-gray-500 dark:text-gray-400" />
        <span className="flex-1 text-xs font-normal text-gray-800 dark:text-gray-200">
          {getStatusText()}
        </span>
        {getStatusIcon()}
        <button
          aria-label={collapsed ? "Expand" : "Collapse"}
          onClick={() => setCollapsed((c) => !c)}
          className="text-muted-foreground hover:text-foreground ml-2 cursor-pointer"
        >
          <ChevronDown
            className={cn(
              "size-4 transition-transform",
              collapsed ? "rotate-0" : "rotate-180",
            )}
          />
        </button>
      </div>
      {/* Only render the rest if not collapsed */}
      {!collapsed && steps && steps.length > 0 && (
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
                          "h-3.5 w-3.5 rounded-full border border-gray-300 dark:border-gray-600",
                        )}
                      />
                    )}
                  </span>
                  <span
                    className={cn(
                      "font-normal",
                      step.status === "error"
                        ? "text-red-500"
                        : "text-gray-800 dark:text-gray-200",
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
    </div>
  );
}
