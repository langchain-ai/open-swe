import { useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";

import { formatElapsed } from "@/lib/utils";

/**
 * Collapses a finished agent turn's working steps (reasoning, tool calls,
 * exploration, edits, …) behind a single "Worked for …" toggle so the
 * transcript shows only the final reply by default.
 */
export function WorkSummary({
  durationMs,
  children,
}: {
  durationMs: number | null;
  children: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const label =
    durationMs && durationMs >= 1000 ? `Worked for ${formatElapsed(durationMs)}` : "Worked";

  return (
    <div className="my-1">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex items-center gap-1 text-left transition-opacity hover:opacity-90"
        aria-expanded={expanded}
      >
        <ChevronRight
          className={`h-3 w-3 text-[color:var(--ui-text-dim)] shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`}
          aria-hidden
        />
        <span className="text-xs text-[color:var(--ui-text-dim)]">{label}</span>
      </button>
      {expanded && (
        <div className="mt-1 ml-1 space-y-2 border-l-2 border-[var(--ui-border)] pl-3">
          {children}
        </div>
      )}
    </div>
  );
}
