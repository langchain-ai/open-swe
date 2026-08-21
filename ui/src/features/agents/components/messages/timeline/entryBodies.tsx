import { memo } from "react"

import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import {
  Terminal,
  TerminalActions,
  TerminalContent,
  TerminalCopyButton,
  TerminalHeader,
  TerminalTitle,
} from "@/components/ai-elements/terminal"

export const ShellEntryBody = memo(function ShellEntryBody({
  chunk,
}: {
  chunk: ToolExecutionChunk
}) {
  const command =
    typeof chunk.input?.command === "string" ? chunk.input.command : ""
  const output = chunk.output ?? ""
  const isRunning = chunk.status === "in_progress"
  const placeholder = isRunning
    ? "Running…"
    : chunk.status === "pending"
      ? "Waiting for approval…"
      : null

  return (
    <Terminal className="text-[12px]" isStreaming={isRunning} output={output}>
      {command && (
        <TerminalHeader className="items-start">
          <TerminalTitle className="min-w-0 flex-1 items-start">
            <span className="font-mono text-[12px] leading-relaxed break-words whitespace-pre-wrap text-foreground/85 select-text">
              <span className="text-muted-foreground/80">$ </span>
              {command}
            </span>
          </TerminalTitle>
          {output && (
            <TerminalActions>
              <TerminalCopyButton aria-label="Copy output" />
            </TerminalActions>
          )}
        </TerminalHeader>
      )}
      {output || isRunning ? (
        <TerminalContent className="max-h-64 p-3 text-[12px] text-muted-foreground" />
      ) : (
        placeholder && (
          <TerminalContent className="p-3 text-[12px]">
            <p
              className={
                chunk.status === "pending"
                  ? "text-warning-foreground"
                  : "text-muted-foreground"
              }
            >
              {placeholder}
            </p>
          </TerminalContent>
        )
      )}
    </Terminal>
  )
})
