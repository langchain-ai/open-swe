import { memo, useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import { MultiFileDiff } from "@pierre/diffs/react";
import { toolHeaderType, toolStatusToHeaderState } from "../messages/aiElements/toolAdapter";
import type { AcpToolKind, ToolExecutionChunk } from "@/lib/agents/types";
import { useDiffOptions } from "@/components/agents/utils/diffUtils";
import { countLineChanges } from "@/components/agents/utils/diffStats";
import { Tool, ToolContent, ToolHeader } from "@/components/ai-elements/tool";

interface ToolExecutionProps {
  chunk: ToolExecutionChunk;
  projectPath?: string;
  onApprove?: (approvalRequestId: string) => void;
  onReject?: (approvalRequestId: string) => void;
  onAutoApprove?: (approvalRequestId: string) => void;
  onOpenDiff?: (diffData: { filePath: string; originalContent: string; modifiedContent: string }) => void;
  resolvedDiffData?: { originalContent: string; modifiedContent: string };
}

function stripProjectPath(path: string, projectPath?: string): string {
  if (!projectPath || !path.startsWith(projectPath)) return path;
  const relative = path.slice(projectPath.length);
  return relative.startsWith("/") ? "." + relative : "./" + relative;
}

function getFileName(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  return parts[parts.length - 1] || path;
}

function formatToolDisplay(
  title: string,
  toolKind: AcpToolKind,
  input: Record<string, unknown> | undefined,
  projectPath?: string,
): string {
  const path = input?.path as string | undefined;
  const pattern = input?.pattern as string | undefined;
  const query = input?.query as string | undefined;
  const url = input?.url as string | undefined;
  const command = input?.command as string | undefined;

  switch (toolKind) {
    case "read": {
      if (path) {
        const displayPath = stripProjectPath(path, projectPath);
        return `Read(${displayPath})`;
      }
      return title;
    }
    case "search": {
      if (pattern) {
        const truncated = pattern.length > 40 ? pattern.slice(0, 40) + "..." : pattern;
        return `Search("${truncated}")`;
      }
      if (query) {
        return `Search("${query.slice(0, 40)}${query.length > 40 ? "..." : ""}")`;
      }
      return title;
    }
    case "fetch": {
      if (url) {
        return `Fetch(${url.slice(0, 50)}${url.length > 50 ? "..." : ""})`;
      }
      return title;
    }
    case "execute": {
      if (command) {
        const truncated = command.length > 60 ? command.slice(0, 60) + "..." : command;
        return `Shell(${truncated})`;
      }
      return title;
    }
    case "edit":
    case "delete":
    case "move":
      return title;
    case "think":
      return "Thinking...";
    default:
      return title;
  }
}

/**
 * The diff body shown inside an edit tool's collapsible content: a file header
 * (path + line-change counts) above a scrollable pierre `MultiFileDiff` with
 * fade-in edge shadows. Kept on the `--ui-*` palette — this is the
 * domain-specific diff viewer, not an AI Elements primitive.
 */
const DiffBox = memo(function DiffBox({
  filePath,
  originalContent,
  newContent,
  additions,
  deletions,
  isError,
}: {
  filePath: string;
  originalContent: string;
  newContent: string;
  additions: number;
  deletions: number;
  isError: boolean;
}) {
  const diffOptions = useDiffOptions();
  const inlineDiffOptions = useMemo(
    () => ({ ...diffOptions, disableFileHeader: true }),
    [diffOptions],
  );
  const scrollRef = useRef<HTMLDivElement>(null);
  const [scrolledFromTop, setScrolledFromTop] = useState(false);
  const [scrolledFromBottom, setScrolledFromBottom] = useState(false);

  const updateScrollIndicators = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setScrolledFromTop(el.scrollTop > 0);
    setScrolledFromBottom(el.scrollTop < el.scrollHeight - el.clientHeight - 1);
  }, []);

  useLayoutEffect(() => {
    updateScrollIndicators();
  }, [updateScrollIndicators]);

  const edgeShadows = [
    scrolledFromTop ? "inset 0 12px 10px -10px rgba(42, 63, 95, 0.95)" : "",
    scrolledFromBottom ? "inset 0 -12px 10px -10px rgba(42, 63, 95, 0.95)" : "",
  ]
    .filter(Boolean)
    .join(", ");

  const oldFile = { name: filePath, contents: originalContent };
  const newFile = { name: filePath, contents: newContent };

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--ui-border-subtle)] bg-[var(--ui-code-bubble)]">
      <div className="flex items-center gap-2 px-3 py-2">
        <span
          className={`min-w-0 flex-1 truncate text-[13px] ${isError ? "text-red-400" : "text-[color:var(--ui-accent)]"}`}
        >
          {filePath}
        </span>
        <span className="flex shrink-0 items-center gap-2 text-xs">
          <span className="text-green-400">+{additions}</span>
          <span className="text-red-400">-{deletions}</span>
        </span>
      </div>
      <div
        ref={scrollRef}
        onScroll={updateScrollIndicators}
        className="max-h-[250px] overflow-auto border-t border-[var(--ui-border)]"
        style={{ boxShadow: edgeShadows || "none" }}
      >
        <MultiFileDiff oldFile={oldFile} newFile={newFile} options={inlineDiffOptions} />
      </div>
    </div>
  );
});

export const ToolExecution = memo(function ToolExecution({
  chunk,
  projectPath,
  resolvedDiffData,
}: ToolExecutionProps) {
  const { title, toolKind, input, status, output } = chunk;
  const diffs = chunk.diffs?.length ? chunk.diffs : chunk.diffData ? [chunk.diffData] : [];
  const diffData = diffs[diffs.length - 1];
  const isEditOp =
    toolKind === "edit" || toolKind === "delete" || toolKind === "move" || diffData != null;
  const state = toolStatusToHeaderState(status);

  if (isEditOp && diffData) {
    const editedFilePath = stripProjectPath(diffData.filePath, projectPath);
    const editedFileName = getFileName(editedFilePath);
    const stats = countLineChanges(
      diffData.originalContent,
      diffData.newContent,
      diffData.filePath,
    );
    const verb = status === "in_progress" ? "Editing" : "Edited";
    const showDiff = status !== "in_progress";

    return (
      <Tool className="mb-0" defaultOpen={false}>
        <ToolHeader
          state={state}
          title={`${verb} ${editedFileName}`}
          type={toolHeaderType(toolKind)}
        />
        {showDiff && (
          <ToolContent>
            <DiffBox
              additions={stats.additions}
              deletions={stats.deletions}
              filePath={editedFilePath || diffData.filePath}
              isError={status === "error"}
              newContent={resolvedDiffData?.modifiedContent ?? diffData.newContent}
              originalContent={resolvedDiffData?.originalContent ?? diffData.originalContent ?? ""}
            />
            {status === "pending" && (
              <p className="text-muted-foreground text-xs">Waiting for approval…</p>
            )}
          </ToolContent>
        )}
      </Tool>
    );
  }

  const displayName = formatToolDisplay(title, toolKind, input, projectPath);
  const trimmedOutput = output?.trim();

  return (
    <Tool className="mb-0" defaultOpen={false}>
      <ToolHeader state={state} title={displayName} type={toolHeaderType(toolKind)} />
      {trimmedOutput && (
        <ToolContent>
          <pre
            className={`max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md p-3 font-mono text-xs ${
              status === "error"
                ? "bg-destructive/10 text-destructive"
                : "bg-muted/50 text-muted-foreground"
            }`}
          >
            {trimmedOutput}
          </pre>
        </ToolContent>
      )}
    </Tool>
  );
});
