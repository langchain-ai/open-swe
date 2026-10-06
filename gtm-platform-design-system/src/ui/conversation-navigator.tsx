"use client";

import { memo, useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent, ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "./cn";
import { Elevated } from "./elevated";
import { POPUP_SURFACE_SHELL } from "./popup-surface";

/**
 * Rules for ConversationNavigator:
 * Keep one tick per loaded exchange, in chronological order with stable IDs.
 * Keep tick positions fixed; proximity changes only horizontal scale and ink.
 * Preview on hover/focus. Navigate only on click, Enter, or Space.
 * The host owns scrolling and must release any tail-follow lock before jumping.
 */
export const CONVERSATION_NAVIGATOR_RULES = [
  "Show from the first loaded question/answer exchange, with one quiet tick per exchange. Never fetch or summarize to fill this index.",
  "Ticks stay vertically fixed. Pointer proximity expands them horizontally with 160ms ease-out-quint transitions.",
  "One compact Markdown preview follows the nearest turn, stays within the host height, and remains hoverable. Links are inert labels; HTML and media are omitted. Escape dismisses it.",
  "Arrow keys, Home, and End move focus without navigating. Enter, Space, or a click explicitly jumps to that turn.",
  "The host scrolls only its transcript and releases tail-follow first. Keyboard and reduced-motion jumps are instant.",
  "Keep this secondary rail in a spare transcript gutter. Narrow and touch-primary hosts retain normal scrolling instead.",
] as const;

export interface ConversationNavigatorItem {
  id: string;
  question: string;
  answer: string;
}

export type ConversationNavigationInput = "pointer" | "keyboard";

interface ConversationNavigatorProps {
  items: readonly ConversationNavigatorItem[];
  activeId?: string | null;
  onNavigate: (id: string, input: ConversationNavigationInput) => void;
  className?: string;
}

interface Preview {
  id: string;
  offset: number;
  input: ConversationNavigationInput;
  open: boolean;
}

const TICK_PITCH = 12;
const PROXIMITY_RADIUS = 4;
const TICK_EXPANSION = 3.5;
const MOTION_CLASS = "transition-[transform,opacity] duration-fast ease-out-quint motion-reduce:transition-none";
const PREVIEW_PLUGINS = [remarkGfm];
const PREVIEW_ELEMENTS = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th", "br", "strong", "em", "del", "code"];

function PreviewBlock({ children }: { children?: ReactNode }) {
  return <span>{children}{" "}</span>;
}

const PREVIEW_COMPONENTS: Components = {
  p: PreviewBlock,
  h1: PreviewBlock,
  h2: PreviewBlock,
  h3: PreviewBlock,
  h4: PreviewBlock,
  h5: PreviewBlock,
  h6: PreviewBlock,
  li: PreviewBlock,
  td: PreviewBlock,
  th: PreviewBlock,
};

const PreviewMarkdown = memo(function PreviewMarkdown({ text, className }: { text: string; className: string }) {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={PREVIEW_PLUGINS}
        allowedElements={PREVIEW_ELEMENTS}
        components={PREVIEW_COMPONENTS}
        unwrapDisallowed
        skipHtml
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});

export function ConversationNavigator({
  items,
  activeId,
  onNavigate,
  className,
}: ConversationNavigatorProps) {
  const rootRef = useRef<HTMLElement>(null);
  const previewRef = useRef<HTMLDivElement>(null);
  const buttonsRef = useRef(new Map<string, HTMLButtonElement>());
  const previewId = useId();
  const [preview, setPreview] = useState<Preview | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [size, setSize] = useState({ height: 0, previewHeight: 0 });

  useEffect(() => {
    const root = rootRef.current;
    const card = previewRef.current;
    if (!root || !card) return;
    const observer = new ResizeObserver(() => {
      setSize({ height: root.clientHeight, previewHeight: card.offsetHeight });
    });
    observer.observe(root);
    observer.observe(card);
    return () => observer.disconnect();
  }, []);

  const index = items.findIndex((item) => item.id === preview?.id);
  const item = items[index];
  const open = preview?.open === true && item !== undefined;
  const railHeight = Math.min(items.length * TICK_PITCH, size.height);
  const pitch = items.length > 0 ? railHeight / items.length : 0;
  const center = (size.height - railHeight) / 2 + (Math.max(0, index) + 0.5) * pitch;
  const previewTop = Math.max(0, Math.min(size.height - size.previewHeight, center - size.previewHeight / 2));
  const tabStopId = items.find((entry) => entry.id === (focusedId ?? activeId))?.id ?? items[0]?.id;

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) {
        setPreview((current) => current ? { ...current, open: false } : null);
      }
    };
    document.addEventListener("keydown", dismiss);
    return () => document.removeEventListener("keydown", dismiss);
  }, [open]);

  function closePreview() {
    setPreview((current) => current ? { ...current, open: false } : null);
  }

  function trackPointer(event: PointerEvent<HTMLDivElement>) {
    if (event.pointerType === "touch" || items.length === 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.height === 0) return;
    const position = Math.max(0, Math.min(items.length - 1, (event.clientY - rect.top) / rect.height * items.length - 0.5));
    const nearest = Math.round(position);
    const next = items[nearest];
    if (next) setPreview({ id: next.id, offset: position - nearest, input: "pointer", open: true });
  }

  function handleKey(event: KeyboardEvent<HTMLButtonElement>, current: number) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closePreview();
      return;
    }
    let next: number;
    switch (event.key) {
      case "ArrowUp": next = Math.max(0, current - 1); break;
      case "ArrowDown": next = Math.min(items.length - 1, current + 1); break;
      case "Home": next = 0; break;
      case "End": next = items.length - 1; break;
      default: return;
    }
    event.preventDefault();
    const target = items[next];
    if (!target) return;
    setPreview({ id: target.id, offset: 0, input: "keyboard", open: true });
    buttonsRef.current.get(target.id)?.focus({ preventScroll: true });
  }

  return (
    <nav
      ref={rootRef}
      aria-label="Conversation contents"
      data-slot="conversation-navigator"
      data-input={preview?.input}
      className={cn("relative flex w-8 flex-col justify-center", className)}
      onPointerLeave={() => { if (preview?.input === "pointer") closePreview(); }}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) {
          setFocusedId(null);
          closePreview();
        }
      }}
    >
      <div
        data-slot="conversation-navigator-ticks"
        className="flex max-h-full min-h-0 flex-col"
        style={{ height: items.length * TICK_PITCH }}
        onPointerMove={trackPointer}
      >
        {items.map((entry, tickIndex) => {
          const distance = open ? Math.abs(tickIndex - index - (preview?.offset ?? 0)) : PROXIMITY_RADIUS;
          const proximity = Math.max(0, 1 - distance / PROXIMITY_RADIUS);
          const selected = entry.id === activeId;
          return (
            <button
              key={entry.id}
              ref={(button) => {
                if (button) buttonsRef.current.set(entry.id, button);
                else buttonsRef.current.delete(entry.id);
              }}
              type="button"
              tabIndex={entry.id === tabStopId ? 0 : -1}
              aria-label={`Turn ${tickIndex + 1}: ${entry.question}`}
              aria-current={selected ? "location" : undefined}
              aria-describedby={open && tickIndex === index ? previewId : undefined}
              className="flex min-h-0 w-full flex-1 cursor-pointer items-center rounded-tick outline-none focus-visible:ring-2 focus-visible:ring-primary"
              onFocus={(event) => {
                setFocusedId(entry.id);
                if (event.currentTarget.matches(":focus-visible")) {
                  setPreview({ id: entry.id, offset: 0, input: "keyboard", open: true });
                }
              }}
              onKeyDown={(event) => handleKey(event, tickIndex)}
              onClick={(event) => onNavigate(entry.id, event.detail === 0 ? "keyboard" : "pointer")}
            >
              <span
                aria-hidden="true"
                data-slot="conversation-navigator-tick"
                className={cn("h-px w-1.5 origin-left rounded-full bg-ink", MOTION_CLASS)}
                style={{
                  transform: `scaleX(${1 + TICK_EXPANSION * proximity ** 1.5})`,
                  opacity: open && tickIndex === index ? 0.9 : Math.max(selected ? 0.65 : 0.25, proximity * 0.5),
                }}
              />
            </button>
          );
        })}
      </div>
      <div
        data-slot="conversation-navigator-preview"
        className={cn("absolute top-0 left-full w-interstitial pl-2", MOTION_CLASS)}
        style={{
          transform: `translateY(${previewTop}px) translateX(${open ? 0 : -4}px)`,
          opacity: open ? 1 : 0,
          pointerEvents: open ? "auto" : "none",
        }}
        aria-hidden={!open}
      >
        <Elevated
          ref={previewRef}
          offset={2}
          shadowLevel={2}
          id={previewId}
          role="tooltip"
          className={cn(POPUP_SURFACE_SHELL, "flex min-w-0 flex-col gap-1 p-3 ring-(length:--ring-width-hairline)")}
        >
          <PreviewMarkdown className="truncate text-label font-medium text-ink" text={item?.question ?? ""} />
          <PreviewMarkdown className="line-clamp-3 min-h-12 text-meta text-ink-subtle" text={item?.answer || "No reply yet"} />
        </Elevated>
      </div>
    </nav>
  );
}
