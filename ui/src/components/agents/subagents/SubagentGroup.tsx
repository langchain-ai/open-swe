import type { ToolExecutionChunk } from "@/lib/agents/types";

import { SubagentCard } from "./SubagentCard";

/** Maximum number of subagent cards rendered per row. */
const MAX_SUBAGENT_COLUMNS = 4;

/**
 * Renders the subagents from a `subagent-group` render item as a responsive
 * card grid. The column count follows the number of cards up to
 * {@link MAX_SUBAGENT_COLUMNS}, so 1–4 subagents fill the row evenly and 5+
 * wrap onto additional rows.
 */
export function SubagentGroup({ chunks }: { chunks: ToolExecutionChunk[] }) {
  const columns = Math.min(Math.max(chunks.length, 1), MAX_SUBAGENT_COLUMNS);
  return (
    <div
      className="grid gap-2"
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {chunks.map((chunk) => (
        <SubagentCard key={chunk.toolCallId} chunk={chunk} />
      ))}
    </div>
  );
}
