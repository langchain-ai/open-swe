"use client";

import { useState } from "react";
import {
  Terminal,
  FileCode,
  Loader2,
  CheckCircle,
  XCircle,
  ChevronDown,
  ChevronUp,
  MessageSquare,
  FileText,
} from "lucide-react";

// Common props for all action types
type BaseActionProps = {
  status: "loading" | "generating" | "done";
  success?: boolean;
  reasoningText?: string;
  summaryText?: string;
};

// Shell command specific props
type ShellActionProps = BaseActionProps & {
  actionType: "shell";
  command: string[];
  workdir?: string;
  output?: string;
  errorCode?: number;
};

// Apply patch specific props
type PatchActionProps = BaseActionProps & {
  actionType: "apply-patch";
  file: string;
  diff?: string;
  errorMessage?: string;
  fixedDiff?: string;
};

// Union type for all possible action props
export type ActionStepProps =
  | (BaseActionProps & { status: "loading" })
  | ShellActionProps
  | PatchActionProps;

export function ActionStep(props: ActionStepProps) {
  const [expanded, setExpanded] = useState(props.status === "done");
  const [showReasoning, setShowReasoning] = useState(false);
  const [showSummary, setShowSummary] = useState(false);

  const getStatusIcon = () => {
    switch (props.status) {
      case "loading":
        return (
          <div className="h-3.5 w-3.5 rounded-full border border-gray-600" />
        );
      case "generating":
        return <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-400" />;
      case "done":
        return props.success ? (
          <CheckCircle className="h-3.5 w-3.5 text-green-500" />
        ) : (
          <XCircle className="h-3.5 w-3.5 text-red-500" />
        );
    }
  };

  const getStatusText = () => {
    if (props.status === "loading") {
      return "Preparing action...";
    }

    if (props.status === "generating") {
      return props.actionType === "shell"
        ? "Executing..."
        : "Applying patch...";
    }

    if (props.status === "done") {
      if (props.actionType === "shell") {
        return props.success ? "Command completed" : "Command failed";
      } else if (props.actionType === "apply-patch") {
        return props.success ? "Patch applied" : "Patch failed";
      }
    }

    return "";
  };

  // Determine if we should show the content toggle button
  const shouldShowToggle = () => {
    if (props.status !== "done") return false;

    if (props.actionType === "shell") {
      return !!props.output;
    } else if (props.actionType === "apply-patch") {
      return !!props.diff;
    }

    return false;
  };

  // Render the header icon based on action type
  const renderHeaderIcon = () => {
    if (props.status === "loading" || !("actionType" in props)) {
      // In loading state, we don't know the type yet, use a generic icon
      return <Loader2 className="mr-2 h-3.5 w-3.5 text-gray-400" />;
    }

    return props.actionType === "shell" ? (
      <Terminal className="mr-2 h-3.5 w-3.5 text-gray-400" />
    ) : (
      <FileCode className="mr-2 h-3.5 w-3.5 text-gray-400" />
    );
  };

  // Render the header content based on action type
  const renderHeaderContent = () => {
    if (props.status === "loading" || !("actionType" in props)) {
      return (
        <span className="text-xs font-normal text-gray-300">
          Preparing action...
        </span>
      );
    }

    if (props.actionType === "shell") {
      return (
        <div className="flex-1">
          {props.workdir && (
            <div className="mb-0.5 text-xs font-normal text-gray-400">
              {props.workdir}
            </div>
          )}
          <code className="text-xs font-normal text-gray-300">
            {props.command.join(" ")}
          </code>
        </div>
      );
    } else {
      return (
        <code className="flex-1 text-xs font-normal text-gray-300">
          {props.file}
        </code>
      );
    }
  };

  // Render the content based on action type
  const renderContent = () => {
    if (props.status !== "done" || !("actionType" in props)) return null;

    if (!expanded) return null;

    if (props.actionType === "shell" && props.output) {
      return (
        <div className="overflow-x-auto bg-gray-900 p-2 text-gray-200">
          <pre className="text-xs font-normal whitespace-pre-wrap">
            {props.output}
          </pre>
          {props.errorCode !== undefined && !props.success && (
            <div className="mt-1 text-xs text-red-400">
              Exit code: {props.errorCode}
            </div>
          )}
        </div>
      );
    } else if (props.actionType === "apply-patch" && props.diff) {
      return (
        <div className="overflow-x-auto bg-gray-900 p-2">
          <pre
            className="text-xs font-normal whitespace-pre-wrap text-gray-200"
            dangerouslySetInnerHTML={{ __html: formatDiff(props.diff) }}
          />

          {!props.success && props.errorMessage && (
            <div className="mt-2 rounded border border-red-700/30 bg-red-900/30 p-2 text-xs text-red-400">
              {props.errorMessage}
            </div>
          )}

          {!props.success && props.fixedDiff && (
            <div className="mt-2">
              <div className="mb-1 text-xs text-gray-400">Fixed diff:</div>
              <pre
                className="text-xs font-normal whitespace-pre-wrap text-gray-200"
                dangerouslySetInnerHTML={{
                  __html: formatDiff(props.fixedDiff),
                }}
              />
            </div>
          )}
        </div>
      );
    }

    return null;
  };

  return (
    <div className="overflow-hidden rounded-md border border-gray-700">
      {props.reasoningText && (
        <div className="border-b border-blue-800 bg-blue-900/50 p-2">
          <button
            onClick={() => setShowReasoning(!showReasoning)}
            className="flex items-center gap-1 text-xs font-normal text-blue-400 hover:text-blue-300"
          >
            <MessageSquare className="h-3 w-3" />
            {showReasoning ? "Hide reasoning" : "Show reasoning"}
          </button>
          {showReasoning && (
            <p className="mt-1 text-xs font-normal text-blue-300">
              {props.reasoningText}
            </p>
          )}
        </div>
      )}

      <div className="flex items-center border-b border-gray-700 bg-gray-800 p-2">
        {renderHeaderIcon()}
        {renderHeaderContent()}
        <div className="flex items-center gap-2">
          <span className="text-xs font-normal text-gray-400">
            {getStatusText()}
          </span>
          {getStatusIcon()}
          {shouldShowToggle() && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-gray-400 hover:text-gray-300"
            >
              {expanded ? (
                <ChevronUp className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
            </button>
          )}
        </div>
      </div>

      {renderContent()}

      {props.summaryText && props.status === "done" && (
        <div className="border-t border-green-800 bg-green-900/50 p-2">
          <button
            onClick={() => setShowSummary(!showSummary)}
            className="flex items-center gap-1 text-xs font-normal text-green-400 hover:text-green-300"
          >
            <FileText className="h-3 w-3" />
            {showSummary ? "Hide summary" : "Show summary"}
          </button>
          {showSummary && (
            <p className="mt-1 text-xs font-normal text-green-300">
              {props.summaryText}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function formatDiff(diff: string) {
  return diff
    .split("\n")
    .map((line) => {
      if (line.startsWith("+")) {
        return `<span class="text-green-400">${line}</span>`;
      } else if (line.startsWith("-")) {
        return `<span class="text-red-400">${line}</span>`;
      } else if (line.startsWith("@")) {
        return `<span class="text-blue-400">${line}</span>`;
      }
      return line;
    })
    .join("\n");
}
