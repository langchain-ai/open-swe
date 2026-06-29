import { memo } from "react";
import type { ReactNode } from "react";
import type { AcpToolStatus, ToolExecutionChunk } from "@/lib/agents/types";
import {
  Terminal,
  TerminalActions,
  TerminalContent,
  TerminalCopyButton,
  TerminalHeader,
  TerminalTitle,
} from "@/components/ai-elements/terminal";

interface ShellCommandProps {
  chunk: ToolExecutionChunk;
  projectPath?: string;
}

function statusPill(status: AcpToolStatus): ReactNode {
  switch (status) {
    case "in_progress":
      return <span className="text-xs text-yellow-400">Running…</span>;
    case "completed":
      return <span className="text-xs text-emerald-400">✓ Success</span>;
    case "error":
      return <span className="text-xs text-red-400">✗ Failed</span>;
    case "pending":
      return <span className="text-xs text-yellow-400">Waiting for approval…</span>;
    default:
      return null;
  }
}

/**
 * Renders an `execute` tool call as an AI Elements Terminal: the command shown
 * as a `$`-prefixed line above its (ANSI-rendered) output, with a status pill
 * and copy button in the header.
 */
export const ShellCommand = memo(function ShellCommand({ chunk }: ShellCommandProps) {
  const command = typeof chunk.input?.command === "string" ? chunk.input.command : "";
  const rawOutput = chunk.output ?? "";
  const body = command ? `$ ${command}${rawOutput ? `\n${rawOutput}` : ""}` : rawOutput;
  const isRunning = chunk.status === "in_progress";

  return (
    <Terminal className="my-1" isStreaming={isRunning} output={body}>
      <TerminalHeader>
        <TerminalTitle>Terminal</TerminalTitle>
        <TerminalActions>
          {statusPill(chunk.status)}
          <TerminalCopyButton />
        </TerminalActions>
      </TerminalHeader>
      <TerminalContent />
    </Terminal>
  );
});
