import { memo, useState, useRef, useCallback, useLayoutEffect } from "react";
import type { ToolExecutionChunk } from "@/lib/agents/types";

interface ShellCommandProps {
  chunk: ToolExecutionChunk;
  projectPath?: string;
}

function getHeaderText(chunk: ToolExecutionChunk): string {
  const cmd = (chunk.input?.command as string) || "";
  const truncated = cmd.length > 80 ? cmd.slice(0, 80) + "..." : cmd;
  if (chunk.status === "in_progress") return `Running ${truncated}`;
  if (chunk.status === "pending") return `Run ${truncated}`;
  return `Background terminal finished with ${truncated}`;
}

export const ShellCommand = memo(function ShellCommand({
  chunk,
}: ShellCommandProps) {
  const isSettled = chunk.status === "completed" || chunk.status === "error";
  const [expanded, setExpanded] = useState(!isSettled);
  const [scrolledFromTop, setScrolledFromTop] = useState(false);
  const [scrolledFromBottom, setScrolledFromBottom] = useState(true);
  const outputRef = useRef<HTMLDivElement>(null);

  const handleOutputScroll = useCallback(() => {
    const el = outputRef.current;
    if (!el) return;
    setScrolledFromTop(el.scrollTop > 0);
    setScrolledFromBottom(el.scrollTop < el.scrollHeight - el.clientHeight - 1);
  }, []);

  const command = (chunk.input?.command as string) || "";
  const output = chunk.output || "";
  const headerText = getHeaderText(chunk);

  useLayoutEffect(() => {
    handleOutputScroll();
  }, [handleOutputScroll, output, expanded]);

  const outputEdgeShadows = [
    scrolledFromTop ? "inset 0 12px 10px -10px rgba(42, 63, 95, 0.95)" : "",
    scrolledFromBottom ? "inset 0 -12px 10px -10px rgba(42, 63, 95, 0.95)" : "",
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <div className="my-1">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="w-full flex items-center gap-2 py-1 text-left hover:opacity-90 transition-opacity"
      >
        <span className="text-[color:var(--ui-text-muted)] text-[12px] truncate flex-1 min-w-0">
          {headerText}
        </span>
        <span
          className="text-[color:var(--ui-text-dim)] text-xs shrink-0 transition-transform"
          style={{ transform: expanded ? "rotate(180deg)" : "rotate(0deg)" }}
        >
          ▾
        </span>
      </button>

      {expanded && (
        <div className="rounded-xl border border-[var(--ui-border-subtle)] bg-[var(--ui-code-bubble)] mt-1 overflow-hidden max-h-[250px] flex flex-col">
          <div className="px-3 pt-2 pb-1 font-mono text-xs shrink-0">
            <div className="flex items-center justify-between gap-2 mb-2">
              <span className="text-[color:var(--ui-accent-2)]">bash</span>
              {chunk.status === "in_progress" && (
                <span className="text-yellow-400 shrink-0">Running...</span>
              )}
              {chunk.status === "completed" && (
                <span className="text-[color:var(--ui-text-muted)] shrink-0">✓ Success</span>
              )}
              {chunk.status === "error" && (
                <span className="text-red-400 shrink-0">✗ Failed</span>
              )}
              {chunk.status === "pending" && (
                <span className="text-yellow-400 shrink-0">Waiting for approval...</span>
              )}
            </div>
            <div className="max-h-[120px] overflow-y-auto">
              <div className="text-[color:var(--ui-text)] font-semibold whitespace-pre overflow-x-auto">
                <span className="text-[color:var(--ui-text-dim)]">$ </span>
                {command}
              </div>
            </div>
          </div>
          {output && (
            <div
              ref={outputRef}
              onScroll={handleOutputScroll}
              className="min-h-0 flex-1 overflow-auto px-3 pb-2"
              style={{ boxShadow: outputEdgeShadows || "none" }}
            >
              <pre className="mt-1 text-[color:var(--ui-text-muted)] whitespace-pre font-mono text-xs w-max min-w-full">
                {output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
});
