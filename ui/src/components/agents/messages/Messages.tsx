// @ts-nocheck — ported from open-swe-app (Electron); strict checks applied when wiring cloud APIs.
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

import { useLiveMarkdownMessageId } from "@/lib/agents/provider/useLiveMarkdownMessageId";

import { AgentMessage } from "./AgentMessage";
import { ThinkingSpinner } from "./ThinkingSpinner";
import { UserMessage } from "./UserMessage";
import type { MessagesProps } from "./types";

const BOTTOM_LOCK_THRESHOLD_PX = 24;

export const Messages = memo(function Messages({
  messages,
  isStreaming,
  streamIsLoading,
  isThinking,
  settingUpSandbox,
  project,
  contentWidthClass = "max-w-[42rem]",
  contentPaddingClass = "px-6",
  bottomInset = 0,
  scrollButtonSlot = "internal",
  onShowScrollToBottomChange,
  scrollControlRef,
  onApprove,
  onReject,
  onAutoApprove,
  onOpenDiff,
}: MessagesProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const autoScrollEnabledRef = useRef(true);
  const lastManualScrollTopRef = useRef(0);
  const previousScrollTopRef = useRef(0);
  const pendingScrollFrameRef = useRef<number | null>(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);

  const clearScheduledScroll = useCallback(() => {
    if (pendingScrollFrameRef.current === null) return;
    window.cancelAnimationFrame(pendingScrollFrameRef.current);
    pendingScrollFrameRef.current = null;
  }, []);

  const isNearBottom = useCallback((el: HTMLDivElement) => {
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    return distanceFromBottom <= BOTTOM_LOCK_THRESHOLD_PX;
  }, []);

  const syncScrollButtonVisibility = useCallback((el: HTMLDivElement) => {
    setShowScrollToBottom(!isNearBottom(el));
  }, [isNearBottom]);

  const scrollToBottomNow = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;

    el.scrollTop = el.scrollHeight;
    const currentTop = el.scrollTop;
    lastManualScrollTopRef.current = currentTop;
    previousScrollTopRef.current = currentTop;
    syncScrollButtonVisibility(el);
  }, [syncScrollButtonVisibility]);

  const scheduleScrollToBottom = useCallback(() => {
    if (!autoScrollEnabledRef.current) return;

    clearScheduledScroll();
    pendingScrollFrameRef.current = window.requestAnimationFrame(() => {
      pendingScrollFrameRef.current = null;
      if (!autoScrollEnabledRef.current) return;
      scrollToBottomNow();
    });
  }, [clearScheduledScroll, scrollToBottomNow]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const handleScroll = () => {
      const currentTop = el.scrollTop;
      const scrolledUp = currentTop < previousScrollTopRef.current - 1;
      const nearBottom = isNearBottom(el);

      if (scrolledUp) {
        autoScrollEnabledRef.current = false;
        clearScheduledScroll();
      } else if (nearBottom) {
        autoScrollEnabledRef.current = true;
      }

      syncScrollButtonVisibility(el);
      lastManualScrollTopRef.current = currentTop;
      previousScrollTopRef.current = currentTop;
    };

    scrollToBottomNow();
    autoScrollEnabledRef.current = true;

    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => {
      el.removeEventListener("scroll", handleScroll);
      clearScheduledScroll();
    };
  }, [clearScheduledScroll, isNearBottom, scrollToBottomNow, syncScrollButtonVisibility]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    if (autoScrollEnabledRef.current) {
      scheduleScrollToBottom();
      return;
    }

    const maxTop = Math.max(0, el.scrollHeight - el.clientHeight);
    const targetTop = Math.min(lastManualScrollTopRef.current, maxTop);
    const jumpDistance = Math.abs(el.scrollTop - targetTop);

    if (jumpDistance > el.clientHeight * 0.5) {
      el.scrollTop = targetTop;
    }

    previousScrollTopRef.current = el.scrollTop;
    syncScrollButtonVisibility(el);
  }, [messages, isStreaming, scheduleScrollToBottom, syncScrollButtonVisibility]);

  useEffect(() => {
    const scroller = scrollRef.current;
    const content = contentRef.current;
    if (!scroller || !content || typeof ResizeObserver === "undefined") return;

    const resizeObserver = new ResizeObserver(() => {
      if (autoScrollEnabledRef.current) {
        scheduleScrollToBottom();
        return;
      }

      const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
      if (lastManualScrollTopRef.current > maxTop) {
        scroller.scrollTop = maxTop;
        lastManualScrollTopRef.current = maxTop;
        previousScrollTopRef.current = maxTop;
      }

      syncScrollButtonVisibility(scroller);
    });

    resizeObserver.observe(scroller);
    resizeObserver.observe(content);

    return () => resizeObserver.disconnect();
  }, [scheduleScrollToBottom, syncScrollButtonVisibility]);

  const visibleMessages = useMemo(() => messages.filter((message) => !message.hidden), [messages]);
  const liveMarkdownMessageId = useLiveMarkdownMessageId(
    visibleMessages,
    streamIsLoading,
    isStreaming,
  );

  const handleScrollToBottom = useCallback(() => {
    autoScrollEnabledRef.current = true;
    clearScheduledScroll();
    scrollToBottomNow();
  }, [clearScheduledScroll, scrollToBottomNow]);

  useEffect(() => {
    if (!scrollControlRef) return;
    scrollControlRef.current = { scrollToBottom: handleScrollToBottom };
    return () => {
      scrollControlRef.current = null;
    };
  }, [handleScrollToBottom, scrollControlRef]);

  useEffect(() => {
    onShowScrollToBottomChange?.(showScrollToBottom);
  }, [onShowScrollToBottomChange, showScrollToBottom]);

  const projectPath = project?.path;

  return (
    <div className="relative flex-1 min-h-0 min-w-0">
      <div
        ref={scrollRef}
        className="h-full min-h-0 min-w-0 overflow-y-auto overflow-x-hidden py-5 text-[13px] leading-6 font-sans antialiased"
      >
        <div
          ref={contentRef}
          className={`w-full ${contentWidthClass} mx-auto min-w-0 ${contentPaddingClass}`}
          style={bottomInset > 0 ? { paddingBottom: bottomInset } : undefined}
        >
          {visibleMessages.map((message, index) => {
            const isLastMessage = index === visibleMessages.length - 1;
            const messageIsStreaming = isStreaming && isLastMessage;
            const messageIsMarkdownLive = message.id === liveMarkdownMessageId;

            if (message.author === "user") {
              return <UserMessage key={message.id} message={message} />;
            }

            return (
              <AgentMessage
                key={message.id}
                message={message}
                isStreaming={messageIsStreaming}
                isMarkdownLive={messageIsMarkdownLive}
                projectPath={projectPath}
                onApprove={onApprove}
                onReject={onReject}
                onAutoApprove={onAutoApprove}
                onOpenDiff={onOpenDiff}
              />
            );
          })}
          <ThinkingSpinner
            isActive={isThinking ?? streamIsLoading ?? isStreaming}
            settingUpSandbox={settingUpSandbox}
          />
        </div>
      </div>

      {scrollButtonSlot === "internal" && showScrollToBottom && (
        <button
          type="button"
          onClick={handleScrollToBottom}
          aria-label="Scroll to bottom"
          className="absolute left-1/2 z-30 inline-flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full bg-[var(--ui-panel-2)] text-[color:var(--ui-text-muted)] shadow-md transition-colors hover:bg-[var(--ui-panel)] hover:text-[color:var(--ui-text)]"
          style={{ bottom: bottomInset > 0 ? bottomInset + 8 : 16 }}
        >
          <ChevronDown className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
});
