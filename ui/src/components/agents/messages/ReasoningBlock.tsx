import { useEffect, useRef, useState } from "react";
import { ChevronRight } from "lucide-react";

function formatElapsed(ms: number): string {
  const secs = Math.max(1, Math.ceil(ms / 1000));
  return secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

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
              className={`h-3 w-3 text-[color:var(--ui-text-dim)] shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`}
              aria-hidden
            />
            <span className="text-xs text-[color:var(--ui-text-dim)]">{reasoningLabel(elapsedMs)}</span>
          </>
        )}
      </button>
      {expanded && trimmed && (
        <div className="mt-1 ml-1 border-l-2 border-[var(--ui-border)] pl-3 text-[12px] leading-5 whitespace-pre-wrap break-words text-[color:var(--ui-text-dim)]">
          {trimmed}
        </div>
      )}
    </div>
  );
}
