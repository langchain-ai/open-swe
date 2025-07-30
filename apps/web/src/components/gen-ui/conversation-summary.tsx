import { FileText, ChevronDown, Check } from "lucide-react";
import { useState } from "react";
import { MarkdownText } from "../thread/markdown-text";
import { cn } from "@/lib/utils";
import { Button } from "../ui/button";
import { Badge } from "../ui/badge";

/**
 * ConversationHistorySummary component for rendering conversation history summaries
 * Styled similarly to ActionItem for UI consistency
 */
export function ConversationHistorySummary({ summary }: { summary: string }) {
  const [expanded, setExpanded] = useState(true);

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
          <FileText className="h-4 w-4 text-white" />
        </div>

        <div className="ml-3 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-foreground text-sm font-semibold">
              Conversation Summary
            </h3>
            <Badge
              variant="secondary"
              className="border-blue-200 bg-blue-100 text-blue-700 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300"
            >
              <Check className="h-3 w-3" />
              Complete
            </Badge>
          </div>
          <p className="text-muted-foreground mt-1 text-xs">
            Summary of the conversation history
          </p>
        </div>

        <Button
          variant="ghost"
          size="sm"
          onClick={() => setExpanded(!expanded)}
          className="h-8 px-2"
        >
          <ChevronDown
            className={cn(
              "h-4 w-4 transition-transform duration-200",
              !expanded && "-rotate-90",
            )}
          />
        </Button>
      </div>

      {/* Content */}
      {expanded && (
        <div className="p-4">
          <div className="text-sm">
            <MarkdownText>{summary}</MarkdownText>
          </div>
        </div>
      )}
    </div>
  );
}

