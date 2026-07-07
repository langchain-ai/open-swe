import { useCallback, useLayoutEffect, useRef, useState } from "react";

import { MessageTimestamp } from "./MessageTimestamp";
import type { Message } from "@/lib/agents/types";

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
    <div className="flex justify-end my-4">
      <div className="max-w-[78%]">
        {images.length > 0 && (
          <div className="flex gap-2 mb-2 flex-wrap justify-end">
            {images.map((img, i) => (
              <img
                key={i}
                src={`data:${img.mimeType};base64,${img.base64}`}
                alt={img.fileName || "image"}
                className="max-w-48 max-h-48 rounded border border-gray-600"
              />
            ))}
          </div>
        )}
        {text && (
          <div className="inline-block max-w-full rounded-2xl bg-[var(--ui-accent-bubble)] overflow-hidden">
            <div
              ref={textRef}
              onScroll={updateScrollIndicators}
              className="max-h-[250px] overflow-auto px-3 py-1.5 text-[color:var(--ui-text)] text-[13px] whitespace-pre-wrap break-words"
              style={{
                maskImage: textEdgeMask,
                WebkitMaskImage: textEdgeMask,
              }}
            >
              {text}
            </div>
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
