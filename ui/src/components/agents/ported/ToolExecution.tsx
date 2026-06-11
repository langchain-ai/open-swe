import { memo, useState, useCallback, useMemo, useRef, useLayoutEffect } from "react";
import { MultiFileDiff } from "@pierre/diffs/react";
import type { ToolExecutionChunk, AcpToolKind } from "@/lib/agents/types";
import { DiffView } from "./DiffView";
import { useDiffOptions } from "@/components/agents/utils/diffUtils";
import { countLineChanges } from "@/components/agents/utils/diffStats";

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

const InlineDiffCollapsible = memo(function InlineDiffCollapsible({
  filePath,
  fileName,
  originalContent,
  newContent,
  additions,
  deletions,
  isError,
}: {
  filePath: string;
  fileName: string;
  originalContent: string;
  newContent: string;
  additions: number;
  deletions: number;
  isError: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const toggle = useCallback(() => setExpanded((prev) => !prev), []);
  const diffOptions = useDiffOptions();
  const inlineDiffOptions = useMemo(
    () => ({ ...diffOptions, disableFileHeader: true }),
    [diffOptions]
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
    if (expanded) updateScrollIndicators();
  }, [expanded, updateScrollIndicators]);

  const edgeShadows = [
    scrolledFromTop ? "inset 0 12px 10px -10px rgba(42, 63, 95, 0.95)" : "",
    scrolledFromBottom ? "inset 0 -12px 10px -10px rgba(42, 63, 95, 0.95)" : "",
  ]
    .filter(Boolean)
    .join(", ");

  const oldFile = { name: filePath, contents: originalContent };
  const newFile = { name: filePath, contents: newContent };

  if (!expanded) {
    return (
      <div className="my-0.5 text-[12px] leading-5">
        <button
          type="button"
          onClick={toggle}
          className="inline-flex items-center gap-1.5 text-left hover:brightness-125 transition-colors"
        >
          <span className={isError ? "text-red-400" : "text-[color:var(--ui-text-muted)]"}>
            Edited <span className="text-[color:var(--ui-accent)]">{fileName}</span>
          </span>
        </button>
      </div>
    );
  }

  return (
    <div className="my-1">
      <div className="my-0.5 text-[12px] leading-5 mb-1.5">
        <button
          type="button"
          onClick={toggle}
          className="inline-flex items-center gap-1.5 text-left hover:brightness-125 transition-colors"
        >
          <span className={isError ? "text-red-400" : "text-[color:var(--ui-text-muted)]"}>
            Edited file
          </span>
          <span className="text-[color:var(--ui-text-dim)] text-[10px]">▾</span>
        </button>
      </div>

      <div className="rounded-lg bg-[var(--ui-code-bubble)] overflow-hidden border border-[var(--ui-border-subtle)]">
        <div className="px-3 py-2 flex items-center gap-2">
          <span className={`text-[13px] truncate flex-1 min-w-0 ${isError ? "text-red-400" : "text-[color:var(--ui-accent)]"}`}>
            {filePath}
          </span>
          <span className="shrink-0 text-xs flex items-center gap-2">
            <span className="text-green-400">+{additions}</span>
            <span className="text-red-400">-{deletions}</span>
          </span>
        </div>

        <div
          ref={scrollRef}
          onScroll={updateScrollIndicators}
          className="border-t border-[var(--ui-border)] max-h-[250px] overflow-auto"
          style={{ boxShadow: edgeShadows || "none" }}
        >
          <MultiFileDiff
            oldFile={oldFile}
            newFile={newFile}
            options={inlineDiffOptions}
          />
        </div>
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
  const diffs = chunk.diffs?.length ? chunk.diffs : (chunk.diffData ? [chunk.diffData] : []);
  const diffData = diffs[diffs.length - 1];

  const isEditOp = toolKind === "edit" || toolKind === "delete" || toolKind === "move" || diffData != null;
  const isCompletedEditOp = isEditOp && diffData && (status === "completed" || status === "error");
  const editedFilePath = diffData ? stripProjectPath(diffData.filePath, projectPath) : "";
  const editedFileName = editedFilePath ? getFileName(editedFilePath) : "";
  const diffStats = diffData ? countLineChanges(diffData.originalContent, diffData.newContent, diffData.filePath) : null;

  if (isCompletedEditOp && diffStats && diffData) {
    return (
      <InlineDiffCollapsible
        filePath={editedFilePath || diffData.filePath}
        fileName={editedFileName || editedFilePath || diffData.filePath}
        originalContent={resolvedDiffData?.originalContent ?? diffData.originalContent ?? ""}
        newContent={resolvedDiffData?.modifiedContent ?? diffData.newContent}
        additions={diffStats.additions}
        deletions={diffStats.deletions}
        isError={status === "error"}
      />
    );
  }

  if (isEditOp && status === "pending" && diffData) {
    return (
      <div className="my-1 text-[12px] leading-5">
        <DiffView diffData={diffData} />
        <span className="text-[color:var(--ui-text-dim)]">Waiting for approval...</span>
      </div>
    );
  }

  if (isEditOp && status === "in_progress") {
    const path = stripProjectPath(
      diffData?.filePath ||
      (input?.filePath as string) ||
      (input?.path as string) || "file",
      projectPath,
    );
    return (
      <div className="my-0.5 text-[12px] leading-5">
        <span className="text-yellow-400">Editing {getFileName(path)}...</span>
      </div>
    );
  }

  const displayName = formatToolDisplay(title, toolKind, input, projectPath);
  const statusTextClass =
    status === "error"
      ? "text-red-400"
      : status === "in_progress" || status === "pending"
        ? "text-yellow-400"
        : "text-[color:var(--ui-text-muted)]";

  return (
    <div className="my-0.5 text-[12px] leading-5">
      <div className="flex items-center gap-2 min-w-0">
        <span className={`${statusTextClass} truncate`}>{displayName}</span>
        {status === "error" && output && (
          <span className="text-red-400/80 truncate">{output.slice(0, 80)}</span>
        )}
      </div>
    </div>
  );
});
