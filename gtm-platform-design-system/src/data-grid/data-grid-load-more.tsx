"use client";

import { useEffect, useRef } from "react";

import { Box } from "../ui/box";

function DataGridLoadMoreSentinel({
  enabled,
  onVisible,
}: {
  enabled: boolean;
  onVisible: () => void;
}) {
  const nodeRef = useRef<HTMLDivElement | null>(null);
  const onVisibleRef = useRef(onVisible);

  useEffect(() => {
    onVisibleRef.current = onVisible;
  }, [onVisible]);

  useEffect(() => {
    if (!enabled) return;
    const node = nodeRef.current;
    if (node === null) return;
    if (typeof IntersectionObserver === "undefined") return;
    const root = node.closest("[data-slot=scroll-area-viewport]");
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) onVisibleRef.current();
      },
      {
        root: root instanceof Element ? root : null,
        rootMargin: "200px",
      }
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [enabled]);

  return (
    <Box
      render={<div ref={nodeRef} />}
      data-slot="table-load-more"
      aria-hidden
      className="h-px w-full"
    />
  );
}

export { DataGridLoadMoreSentinel };
