import { Bot, Check, File, Loader2, Search, X } from "lucide-react";
import { summarizeExploration } from "../renderItems";
import type { ReactNode } from "react";

import type { AcpToolStatus, ToolExecutionChunk } from "@/lib/agents/types";
import {
  Task,
  TaskContent,
  TaskItem,
  TaskItemFile,
  TaskTrigger,
} from "@/components/ai-elements/task";
import { useIsInAgentThreadStream } from "@/lib/agents/provider/useIsInAgentThreadStream";
import { SubagentActivity } from "@/components/agents/subagents/SubagentActivity";

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function exploredLine(chunk: ToolExecutionChunk): ReactNode {
  const input = chunk.input ?? {};
  if (chunk.toolKind === "search") {
    const pattern = asString(input.pattern) || asString(input.query);
    return (
      <span className="inline-flex items-center gap-1.5">
        Searched
        {pattern && (
          <TaskItemFile>
            <Search className="size-3" />
            {pattern.length > 48 ? pattern.slice(0, 48) + "…" : pattern}
          </TaskItemFile>
        )}
      </span>
    );
  }
  const path = asString(input.path) || asString(input.file_path) || asString(input.target_file);
  const fileName = path ? (path.split("/").filter(Boolean).pop() ?? path) : "";
  return (
    <span className="inline-flex items-center gap-1.5">
      Read
      {fileName && (
        <TaskItemFile>
          <File className="size-3" />
          {fileName}
        </TaskItemFile>
      )}
    </span>
  );
}

/**
 * A run of `read`/`search` tool calls collapsed into a single AI Elements Task
 * ("Explored N files") whose items are file/query chips. Open state is
 * controlled by the parent so it can auto-expand while streaming and collapse
 * when the turn finishes.
 */
export function ExploredTask({
  chunks,
  open,
  onOpenChange,
}: {
  chunks: Array<ToolExecutionChunk>;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Task onOpenChange={onOpenChange} open={open}>
      <TaskTrigger title={summarizeExploration(chunks)} />
      <TaskContent>
        {chunks.map((chunk, index) => (
          <TaskItem key={chunk.toolCallId || `explored-${index}`}>{exploredLine(chunk)}</TaskItem>
        ))}
      </TaskContent>
    </Task>
  );
}

function StatusIcon({ status }: { status: AcpToolStatus }) {
  if (status === "completed") {
    return <Check className="size-3.5 shrink-0 text-emerald-500" aria-hidden />;
  }
  if (status === "error") {
    return <X className="size-3.5 shrink-0 text-red-500" aria-hidden />;
  }
  return <Loader2 className="size-3.5 shrink-0 animate-spin text-muted-foreground" aria-hidden />;
}

function SubagentTaskItem({ chunk }: { chunk: ToolExecutionChunk }) {
  const inLiveStream = useIsInAgentThreadStream();
  const input = chunk.input ?? {};
  const subagentType = asString(input.subagent_type) || "subagent";
  const description = asString(input.description);
  const namespace = chunk.subagentNamespace;

  return (
    <TaskItem className="flex min-w-0 flex-col gap-1">
      <div className="flex min-w-0 items-center gap-1.5">
        <StatusIcon status={chunk.status} />
        <span className="truncate font-medium text-foreground">{subagentType}</span>
      </div>
      {description && (
        <p className="whitespace-pre-wrap break-words text-muted-foreground">{description}</p>
      )}
      {inLiveStream && namespace && namespace.length > 0 && (
        <SubagentActivity namespace={namespace} />
      )}
    </TaskItem>
  );
}

/**
 * One or more `task` (subagent) tool calls grouped into a single AI Elements
 * Task, with each subagent as an item showing its type, task description, and
 * live nested activity.
 */
export function SubagentTask({ chunks }: { chunks: Array<ToolExecutionChunk> }) {
  const done = chunks.filter((chunk) => chunk.status === "completed").length;
  const title = chunks.length > 1 ? `Subagents (${done}/${chunks.length})` : "Subagent";

  return (
    <Task defaultOpen>
      <TaskTrigger title={title}>
        <div className="flex cursor-pointer items-center gap-2 text-muted-foreground text-sm transition-colors hover:text-foreground">
          <Bot className="size-4" />
          <span>{title}</span>
        </div>
      </TaskTrigger>
      <TaskContent>
        {chunks.map((chunk, index) => (
          <SubagentTaskItem chunk={chunk} key={chunk.toolCallId || `subagent-${index}`} />
        ))}
      </TaskContent>
    </Task>
  );
}
