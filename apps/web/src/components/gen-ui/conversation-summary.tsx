import { FileText, ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { MarkdownText } from "../thread/markdown-text";
import { ToolIconWithTooltip } from "./tool-icon-tooltip";

/**
 * ConversationHistorySummary component for rendering conversation history summaries
 * Styled similarly to ActionItem for UI consistency
 */
export function ConversationHistorySummary({ summary }: { summary: string }) {
  const [expanded, setExpanded] = useState(true);

  return (
    <div className="border-border overflow-hidden rounded-md border">
      <div className="border-b border-blue-300 bg-blue-100/50 p-2 dark:border-blue-800 dark:bg-blue-900/50">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ToolIconWithTooltip
              toolNamePretty="Conversation Summary"
              icon={<FileText className="h-4 w-4" />}
            />
            <span className="text-xs font-medium">Conversation Summary</span>
          </div>
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1 text-xs font-normal text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
          >
            {expanded ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            {expanded ? "Collapse" : "Expand"}
          </button>
        </div>
      </div>

      {expanded && (
        <div className="p-3">
          <div className="text-sm">
            <MarkdownText>{summary}</MarkdownText>
          </div>
        </div>
      )}
    </div>
  );
}
