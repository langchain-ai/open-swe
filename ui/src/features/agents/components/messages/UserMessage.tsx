import { useCallback, useLayoutEffect, useRef, useState } from "react";

import { MessageTimestamp } from "./MessageTimestamp";
import type { Message } from "@/features/agents/lib/types";

export function UserMessage({ message }: { message: Message }) {
  const text = message.chunks
    .filter((c) => c.kind === "text")
    .map((c) => c.text)
    .join("");

  const images = message.chunks.filter((c) => c.kind === "image");
  const textRef = useRef<HTMLDivElement>(null);
  const [scrolledFromTop, setScrolledFromTop] = useState(false);
  const [scrolledFromBottom, setScrolledFromBottom] = useState(false);

  const updateScrollIndicators = useCallback(() => {
    const el = textRef.current;
    if (!el) return;
    setScrolledFromTop(el.scrollTop > 0);
    setScrolledFromBottom(el.scrollTop < el.scrollHeight - el.clientHeight - 1);
  }, []);

  useLayoutEffect(() => {
    updateScrollIndicators();
  }, [text, updateScrollIndicators]);

  const topStop = scrolledFromTop ? "transparent 0, black 24px" : "black 0";
  const bottomStop = scrolledFromBottom
    ? "black calc(100% - 24px), transparent 100%"
    : "black 100%";
  const textEdgeMask =
    scrolledFromTop || scrolledFromBottom
      ? `linear-gradient(to bottom, ${topStop}, ${bottomStop})`
      : undefined;

  return (
    <div className="group/turn my-4 flex flex-col items-end gap-1">
      <div className="max-w-[80%]">
        {(text || images.length > 0) && (
          <div className="relative overflow-hidden rounded-2xl bg-accent p-3">
            {images.length > 0 && (
              <div className="mb-2 grid max-w-[420px] grid-cols-2 gap-2">
                {images.map((img, i) => (
                  <div
                    key={i}
                    className="overflow-hidden rounded-lg border border-border/80 bg-background/70"
                  >
                    <img
                      src={`data:${img.mimeType};base64,${img.base64}`}
                      alt={img.fileName || "image"}
                      className="block h-auto max-h-[220px] w-full object-cover"
                    />
                  </div>
                ))}
              </div>
            )}
            {text && (
              <div
                ref={textRef}
                onScroll={updateScrollIndicators}
                className="max-h-[250px] overflow-auto whitespace-pre-wrap break-words text-[13px] leading-6 text-accent-foreground"
                style={{
                  maskImage: textEdgeMask,
                  WebkitMaskImage: textEdgeMask,
                }}
              >
                {text}
              </div>
            )}
          </div>
        )}
        {!message.timestampIsFallback && (
          <MessageTimestamp
            timestamp={message.timestamp}
            align="right"
            className="mt-1 pr-1"
          />
        )}
      </div>
    </div>
  );
}
