import { memo, useState, useRef, useCallback, useEffect, useLayoutEffect } from "react";
import type { ToolExecutionChunk } from "@/lib/agents/types";

interface ShellCommandProps {
  chunk: ToolExecutionChunk;
  projectPath?: string;
  /** True while this command is the agent's current (last, streaming) step. */
  isActive?: boolean;
}

function getHeaderText(chunk: ToolExecutionChunk): string {
  const cmd = (chunk.input?.command as string) || "";
  const truncated = cmd.length > 80 ? cmd.slice(0, 80) + "..." : cmd;
  if (chunk.status === "in_progress") return `Running ${truncated}`;
  if (chunk.status === "pending") return `Run ${truncated}`;
  return `Ran ${truncated}`;
}

export const ShellCommand = memo(function ShellCommand({
  chunk,
  isActive = false,
}: ShellCommandProps) {
  const isSettled = chunk.status === "completed" || chunk.status === "error";
  const autoExpanded = !isSettled || isActive;
  const [expanded, setExpanded] = useState(autoExpanded);
  const prevAutoExpandedRef = useRef(autoExpanded);
  const [scrolledFromTop, setScrolledFromTop] = useState(false);
  const [scrolledFromBottom, setScrolledFromBottom] = useState(true);
  const outputRef = useRef<HTMLDivElement>(null);

  const handleOutputScroll = useCallback(() => {
    const el = outputRef.current;
    if (!el) return;
    setScrolledFromTop(el.scrollTop > 0);
    setScrolledFromBottom(el.scrollTop < el.scrollHeight - el.clientHeight - 1);
  }, []);

  // Follow the active step: expand while this command is the current process,
  // collapse once the agent moves on. Manual toggles still win until that flips.
  useEffect(() => {
    if (prevAutoExpandedRef.current !== autoExpanded) {
      setExpanded(autoExpanded);
      prevAutoExpandedRef.current = autoExpanded;
    }
  }, [autoExpanded]);

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
        <div className="rounded-xl bg-[var(--ui-accent-bubble)] mt-1 overflow-hidden max-h-[250px] flex flex-col">
          <div className="px-3 pt-2 pb-1 font-mono text-xs shrink-0">
            <div className="text-[color:var(--ui-text-dim)] mb-2">bash</div>
            <div className="text-[color:var(--ui-text)] font-semibold whitespace-pre overflow-x-auto">
              <span className="text-[color:var(--ui-text-dim)]">$ </span>
              {command}
            </div>
          </div>
          {output && (
            <div
              ref={outputRef}
              onScroll={handleOutputScroll}
              className="min-h-0 flex-1 overflow-auto px-3 pb-1"
              style={{ boxShadow: outputEdgeShadows || "none" }}
            >
              <pre className="mt-1 text-[color:var(--ui-text-muted)] whitespace-pre font-mono text-xs w-max min-w-full">
                {output}
              </pre>
            </div>
          )}
          <div className="px-3 py-1.5 flex justify-end shrink-0">
            {chunk.status === "in_progress" && (
              <span className="text-yellow-400 text-xs">Running...</span>
            )}
            {chunk.status === "completed" && (
              <span className="text-[color:var(--ui-text-muted)] text-xs">✓ Success</span>
            )}
            {chunk.status === "error" && (
              <span className="text-red-400 text-xs">✗ Failed</span>
            )}
            {chunk.status === "pending" && (
              <span className="text-yellow-400 text-xs">Waiting for approval...</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
});
