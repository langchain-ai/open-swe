"use client";

import { useState } from "react";
import {
  AlertTriangle,
  Loader2,
  CheckCircle,
  MessageSquare,
} from "lucide-react";

type DiagnoseErrorActionProps = {
  status: "loading" | "generating" | "done";
  diagnosis?: string;
  reasoningText?: string;
};

export function DiagnoseErrorAction({
  status,
  diagnosis,
  reasoningText,
}: DiagnoseErrorActionProps) {
  const [showReasoning, setShowReasoning] = useState(false);

  const getStatusIcon = () => {
    switch (status) {
      case "loading":
        return <div className="border-border size-3.5 rounded-full border" />;
      case "generating":
        return (
          <Loader2 className="text-muted-foreground size-3.5 animate-spin" />
        );
      case "done":
        return <CheckCircle className="size-3.5 text-green-500" />;
    }
  };

  const getStatusText = () => {
    switch (status) {
      case "loading":
        return "Preparing error analysis...";
      case "generating":
        return "Diagnosing errors...";
      case "done":
        return "Error diagnosis complete";
    }
  };

  return (
    <div className="border-border overflow-hidden rounded-md border">
      {reasoningText && (
        <div className="border-b border-blue-100 bg-blue-50 p-2 dark:border-blue-800 dark:bg-blue-950">
          <button
            onClick={() => setShowReasoning(!showReasoning)}
            className="flex items-center gap-1 text-xs font-normal text-blue-700 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300"
          >
            <MessageSquare className="h-3 w-3" />
            {showReasoning ? "Hide reasoning" : "Show reasoning"}
          </button>
          {showReasoning && (
            <p className="mt-1 text-xs font-normal text-blue-800 dark:text-blue-300">
              {reasoningText}
            </p>
          )}
        </div>
      )}

      <div className="border-border flex items-center border-b bg-gray-50 p-2 dark:bg-gray-800">
        <AlertTriangle className="mr-2 size-3.5 text-amber-500" />
        <span className="text-foreground/80 flex-1 text-xs font-normal">
          {getStatusText()}
        </span>
        {getStatusIcon()}
      </div>

      {status === "done" && diagnosis && (
        <div className="p-2">
          <div className="mb-2">
            <h3 className="text-muted-foreground mb-1 text-xs font-normal">
              Diagnosis
            </h3>
            <p className="text-foreground/80 text-xs font-normal">
              {diagnosis}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
