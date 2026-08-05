import { useEffect, useRef, useState } from "react";
import { ChevronRight } from "lucide-react";

import { formatElapsed } from "@/lib/utils";

function reasoningLabel(elapsedMs: number | null): string {
  if (elapsedMs === null) return "Thought";
  if (elapsedMs < 1000) return "Thought briefly";
  return `Thought for ${formatElapsed(elapsedMs)}`;
}

/**
 * Renders a model's reasoning ("thinking") tokens. While the reasoning is live
 * it streams in muted gray text under a shimmering "Thinking…" header; once the
 * reasoning ends it auto-collapses into a "Thought for …" toggle the user can
 * expand on demand.
 */
export function ReasoningBlock({ text, isLive }: { text: string; isLive: boolean }) {
  const [userExpanded, setUserExpanded] = useState(false);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const wasLiveRef = useRef(false);

  useEffect(() => {
    if (isLive) {
      if (startedAtRef.current === null) startedAtRef.current = Date.now();
      wasLiveRef.current = true;
      return;
    }
    if (wasLiveRef.current && startedAtRef.current !== null) {
      setElapsedMs(Date.now() - startedAtRef.current);
      wasLiveRef.current = false;
    }
  }, [isLive]);

  const trimmed = text.trim();
  if (!trimmed && !isLive) return null;

  const expanded = isLive || userExpanded;

  return (
    <div className="my-1">
      <button
        type="button"
        onClick={() => {
          if (!isLive) setUserExpanded((value) => !value);
        }}
        className="flex items-center gap-1 text-left transition-opacity hover:opacity-90 disabled:cursor-default"
        aria-expanded={expanded}
        disabled={isLive}
      >
        {isLive ? (
          <span className="shimmer-text text-xs">Thinking...</span>
        ) : (
          <>
            <ChevronRight
              className={`size-3 shrink-0 text-muted-foreground/65 transition-transform ${expanded ? "rotate-90" : ""}`}
              aria-hidden
            />
            <span className="text-xs text-muted-foreground">{reasoningLabel(elapsedMs)}</span>
          </>
        )}
      </button>
      {expanded && trimmed && (
        <div className="ms-1 mt-1 whitespace-pre-wrap break-words border-s border-border/45 ps-3 text-[12px] leading-5 text-muted-foreground">
          {trimmed}
        </div>
      )}
    </div>
  );
}
