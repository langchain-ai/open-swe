"use client";

import { useState } from "react";
import {
  CheckCircle,
  Loader2,
  ChevronDown,
  ChevronUp,
  FileText,
} from "lucide-react";
import { cn } from "@/lib/utils";

type WriteTechnicalNotesProps = {
  status: "generating" | "done";
  notes?: string;
  reasoningText?: string;
};

export function WriteTechnicalNotes({
  status,
  notes,
  reasoningText,
}: WriteTechnicalNotesProps) {
  const [expanded, setExpanded] = useState(false);

  const getStatusIcon = () => {
    switch (status) {
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
      case "generating":
        return "Writing technical notes...";
      case "done":
        return "Technical notes written";
    }
  };

  const shouldShowToggle = () => {
    return status === "done" && notes;
  };

  return (
    <div className="border-border overflow-hidden rounded-md border">
      <div className="border-border flex items-center border-b bg-gray-50 p-2 dark:bg-gray-800">
        <FileText className="text-muted-foreground mr-2 size-3.5" />
        <span className="text-foreground/80 flex-1 text-xs font-normal">
          Technical Notes
        </span>
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground text-xs font-normal">
            {getStatusText()}
          </span>
          {getStatusIcon()}
          {shouldShowToggle() && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-muted-foreground hover:text-foreground"
            >
              {expanded ? (
                <ChevronUp className="size-3.5" />
              ) : (
                <ChevronDown className="size-3.5" />
              )}
            </button>
          )}
        </div>
      </div>

      {expanded && notes && status === "done" && (
        <div className="bg-muted overflow-x-auto p-2 dark:bg-gray-900">
          <pre className="text-foreground/90 text-xs font-normal whitespace-pre-wrap">
            {notes}
          </pre>
        </div>
      )}

      {reasoningText && status === "done" && (
        <div className="border-t border-blue-300 bg-blue-100/50 p-2 dark:border-blue-800 dark:bg-blue-900/50">
          <p className="text-xs font-normal text-blue-700 dark:text-blue-300">
            {reasoningText}
          </p>
        </div>
      )}
    </div>
  );
}
