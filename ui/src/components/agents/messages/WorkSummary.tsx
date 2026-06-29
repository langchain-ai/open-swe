import { ChevronDownIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Task, TaskContent, TaskTrigger } from "@/components/ai-elements/task";
import { formatElapsed } from "@/lib/utils";

/**
 * Collapses a finished agent turn's working steps (reasoning, tool calls,
 * exploration, edits, …) behind a single "Worked for …" toggle so the
 * transcript shows only the final reply by default. Built on the AI Elements
 * Task collapsible.
 */
export function WorkSummary({
  durationMs,
  children,
}: {
  durationMs: number | null;
  children: ReactNode;
}) {
  const label =
    durationMs && durationMs >= 1000 ? `Worked for ${formatElapsed(durationMs)}` : "Worked";

  return (
    <Task className="my-1" defaultOpen={false}>
      <TaskTrigger title={label}>
        <div className="flex cursor-pointer items-center gap-2 text-muted-foreground text-sm transition-colors hover:text-foreground">
          <ChevronDownIcon className="size-4 transition-transform group-data-[state=open]:rotate-180" />
          <span>{label}</span>
        </div>
      </TaskTrigger>
      <TaskContent>{children}</TaskContent>
    </Task>
  );
}
