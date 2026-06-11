import { memo } from "react";
import { Bot, Loader2 } from "lucide-react";

import { useIsInAgentThreadStream } from "@/lib/agents/provider/useIsInAgentThreadStream";
import type { ToolExecutionChunk } from "@/lib/agents/types";

import { SubagentActivity } from "./SubagentActivity";

/** Coerce an unknown tool-argument value to a trimmed string, or `""`. */
function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/**
 * A single subagent spawned via the `task` tool. Shows the subagent type and
 * the task input (its `description`) as a compact rectangle.
 */
export const SubagentCard = memo(function SubagentCard({ chunk }: { chunk: ToolExecutionChunk }) {
  const inLiveStream = useIsInAgentThreadStream();
  const input = chunk.input ?? {};
  const subagentType = asString(input.subagent_type) || "subagent";
  const description = asString(input.description);
  const isRunning = chunk.status === "in_progress" || chunk.status === "pending";
  const isError = chunk.status === "error";
  const namespace = chunk.subagentNamespace;
  const activity =
    inLiveStream && namespace && namespace.length > 0 ? (
      <SubagentActivity namespace={namespace} />
    ) : null;

  return (
    <div className="flex min-w-0 flex-col gap-1.5 overflow-hidden rounded-lg border border-[var(--ui-border)] bg-[var(--ui-accent-bubble)] p-2.5">
      <div className="flex items-center gap-1.5 min-w-0">
        {isRunning ? (
          <Loader2 className="h-3 w-3 shrink-0 animate-spin text-[color:var(--ui-accent)]" aria-hidden />
        ) : (
          <Bot
            className={`h-3 w-3 shrink-0 ${isError ? "text-red-400" : "text-[color:var(--ui-accent)]"}`}
            aria-hidden
          />
        )}
        <span className="truncate text-[11px] font-medium text-[color:var(--ui-text-muted)]">
          {subagentType}
        </span>
      </div>
      {description && (
        <p className="line-clamp-5 whitespace-pre-wrap break-words text-[11px] leading-4 text-[color:var(--ui-text-dim)]">
          {description}
        </p>
      )}
      {activity}
    </div>
  );
});
