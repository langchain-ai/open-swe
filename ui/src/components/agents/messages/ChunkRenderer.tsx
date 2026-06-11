import { CodeBlock } from "@/components/agents/ported/CodeBlock";
import { Markdown } from "@/components/agents/ported/Markdown";
import { ToolExecution } from "@/components/agents/ported/ToolExecution";
import type { Chunk } from "@/lib/agents/types";

import type { ApprovalCallbacks } from "./types";

export function ChunkRenderer({
  chunk,
  projectPath,
  isMarkdownLive,
  ...callbacks
}: { chunk: Chunk; projectPath?: string; isMarkdownLive?: boolean } & ApprovalCallbacks) {
  switch (chunk.kind) {
    case "text":
      return (
        <div className="text-[color:var(--ui-text)]">
          <Markdown content={chunk.text} isLive={isMarkdownLive} />
        </div>
      );
    case "code":
      return <CodeBlock text={chunk.text} language={chunk.language} />;
    case "error":
      return <span className="text-red-400">{chunk.text}</span>;
    case "list":
      return (
        <div className="text-gray-300 ml-2">
          {chunk.lines.map((line, i) => (
            <div key={i}>- {line}</div>
          ))}
        </div>
      );
    case "tool-execution":
      return (
        <ToolExecution
          chunk={chunk}
          projectPath={projectPath}
          onApprove={callbacks.onApprove}
          onReject={callbacks.onReject}
          onAutoApprove={callbacks.onAutoApprove}
          onOpenDiff={callbacks.onOpenDiff}
        />
      );
    case "image":
      return (
        <img
          src={`data:${chunk.mimeType};base64,${chunk.base64}`}
          alt={chunk.fileName || "image"}
          className="max-w-48 max-h-48 rounded border border-gray-600"
        />
      );
  }
}
