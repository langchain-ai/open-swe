"use client";

/*
 * Scroll fade container, retokenized onto CORE 14.
 *
 * The two fades are masked overlays painted in the container's own background,
 * so `fadeBg` stays a class string the caller supplies; it defaults to the
 * canvas fill. Fade depth is named rather than free: 16 / 24 / 40, the last
 * being one data row tall.
 *
 * THE FADE IS NOT A SUBSTITUTE FOR THE SCROLLBAR (wiki 02). This file used to
 * run its own raw `overflow-y-auto` box with an optional `hideScrollbar` that
 * deleted the bar outright, on the theory that the gradient already says "there
 * is more". A gradient says there is more; it never says how much more, or where
 * in it you are, and it cannot be dragged. The scroll box is a ScrollArea now,
 * so the overlay thumb and the fade do their separate jobs, and `hideScrollbar`
 * is gone rather than renamed -- there is no longer a setting that hides it.
 *
 * The fades sit outside the ScrollArea and read its viewport through
 * `viewportRef`, because the viewport is the element that scrolls. `ref` still
 * resolves to that same element, so a caller that scrolls this container
 * programmatically keeps the handle it always had.
 *
 * THE CALLER CAPS THE HOST, THE BODY SCROLLS. `max-h-*` on this root used to
 * clip while a `h-full` viewport inside it grew with the content, so a long
 * subagent thread looked truncated and could not be scrolled. The root is a
 * flex column now and the child is `ScrollAreaBody`, the same pairing popups
 * use when they only have a max-height.
 */

import {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useRef,
  type ReactNode,
} from "react";

import { cn } from "./cn";
import { ScrollAreaBody } from "./scroll-area";

type FadeSize = "sm" | "md" | "lg";

interface ScrollFadeContainerProps {
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
  fadeSize?: FadeSize;
  disabled?: boolean;
  fadeBg?: string;
}

const FADE_HEIGHT_CLASS: Record<FadeSize, string> = {
  sm: "h-4",
  md: "h-6",
  lg: "h-row-data",
};

export const ScrollFadeContainer = forwardRef<
  HTMLDivElement,
  ScrollFadeContainerProps
>(function ScrollFadeContainer(
  {
    children,
    className,
    style,
    fadeSize = "md",
    disabled = false,
    fadeBg = "bg-canvas",
  },
  ref,
) {
  const innerRef = useRef<HTMLDivElement>(null);
  const topRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const cleanupRef = useRef<(() => void) | null>(null);

  useImperativeHandle(ref, () => innerRef.current as HTMLDivElement);

  const updateFades = useCallback((container: HTMLElement) => {
    const { scrollTop, scrollHeight, clientHeight } = container;
    const atTop = scrollTop <= 0;
    const atBottom = scrollTop + clientHeight >= scrollHeight - 1;

    if (topRef.current) {
      topRef.current.style.opacity = atTop ? "0" : "1";
    }
    if (bottomRef.current) {
      bottomRef.current.style.opacity = atBottom ? "0" : "1";
    }
  }, []);

  const viewportRef = useCallback(
    (el: HTMLDivElement | null) => {
      cleanupRef.current?.();
      cleanupRef.current = null;
      (innerRef as React.MutableRefObject<HTMLDivElement | null>).current = el;
      if (!el) return;
      updateFades(el);
      const handleScroll = () => updateFades(el);
      el.addEventListener("scroll", handleScroll, { passive: true });
      const ro = new ResizeObserver(() => updateFades(el));
      ro.observe(el);
      cleanupRef.current = () => {
        el.removeEventListener("scroll", handleScroll);
        ro.disconnect();
      };
    },
    [updateFades],
  );

  const fadeHeight = FADE_HEIGHT_CLASS[fadeSize];

  return (
    <div
      className={cn("relative flex flex-col overflow-hidden", className)}
      style={style}
    >
      <ScrollAreaBody overflow="vertical" viewportRef={viewportRef}>
        {children}
      </ScrollAreaBody>
      {!disabled && (
        <div
          ref={topRef}
          className={cn(
            `pointer-events-none absolute inset-x-0 top-0 z-10 ${fadeBg} opacity-0 transition-opacity duration-fast ease-out-quint`,
            "[mask-image:linear-gradient(black_20%,transparent)]",
            fadeHeight,
          )}
        />
      )}
      {!disabled && (
        <div
          ref={bottomRef}
          className={cn(
            `pointer-events-none absolute inset-x-0 bottom-0 z-10 ${fadeBg} opacity-0 transition-opacity duration-fast ease-out-quint`,
            "[mask-image:linear-gradient(transparent,black_80%)]",
            fadeHeight,
          )}
        />
      )}
    </div>
  );
});
