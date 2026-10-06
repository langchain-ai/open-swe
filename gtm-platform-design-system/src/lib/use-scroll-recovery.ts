"use client";

import { useCallback, useEffect, useState } from "react";
import { z } from "zod";
import { readRecovery, useRecoveryPrincipal, useRecoveryView, writeRecovery } from "../lib/browser-recovery";

const positionSchema = z.object({
  top: z.number().finite().min(0).max(10_000_000),
  left: z.number().finite().min(0).max(10_000_000),
  anchor: z.string().max(512).nullable(),
  offset: z.number().finite(),
});

function restorePosition(node: HTMLDivElement, top: number, left: number): void {
  node.scrollTop = top;
  node.scrollLeft = left;
}

/** Restore only product scroll regions. Menus, popovers, and new chat streams opt out. */
export function useScrollRecovery(region: string | undefined): (node: HTMLDivElement | null) => void {
  const principal = useRecoveryPrincipal();
  const route = useRecoveryView();
  const url = new URL(route || "/", "https://recovery.invalid");
  // Conversations opens a conversation from its list, and a draft from a batch's list.
  const listSelection: Record<string, readonly string[]> = {
    "/accounts": ["account"], "/accounts/opportunities": ["opportunity"],
    "/alerts": ["alert"], "/accounts/intelligence": ["edition"],
    "/meetings": ["report"], "/conversations": ["conversation", "draft"], "/inbox": ["conversation", "draft"],
  };
  // Selecting a record must not move its list. A reader pane keeps the record in its key.
  if (["grid", "opportunities", "conversations:list", "conversations:batch"].includes(region ?? "")) {
    for (const selected of listSelection[url.pathname] ?? []) url.searchParams.delete(selected);
  }
  url.searchParams.delete("cursor");
  const view = `${url.pathname}${url.search}`;
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  const ref = useCallback((value: HTMLDivElement | null) => setNode(value), []);
  useEffect(() => {
    if (principal === null || region === undefined || node === null) return;
    const key = `scroll:${view}:${region}`;
    const saved = readRecovery(principal, key, positionSchema);
    let restoring = saved !== undefined;
    let frame = 0;
    let growths = 0;
    let previousHeight = node.scrollHeight;
    const rows = () => Array.from(node.querySelectorAll<HTMLElement>("[data-row-id]"));
    const remember = () => {
      if (restoring || !node.isConnected) return;
      const top = node.getBoundingClientRect().top;
      const anchor = rows().find((row) => row.getBoundingClientRect().bottom > top);
      writeRecovery(principal, key, {
        top: node.scrollTop, left: node.scrollLeft,
        anchor: anchor?.dataset.rowId ?? null,
        offset: anchor === undefined ? 0 : anchor.getBoundingClientRect().top - top,
      });
    };
    const restore = () => {
      if (!restoring || saved === undefined) return;
      if (node.scrollHeight !== previousHeight) { growths += 1; previousHeight = node.scrollHeight; }
      const anchor = saved.anchor === null ? undefined : rows().find((row) => row.dataset.rowId === saved.anchor);
      const desired = anchor === undefined ? saved.top : node.scrollTop + anchor.getBoundingClientRect().top - node.getBoundingClientRect().top - saved.offset;
      restorePosition(node, desired, saved.left);
      if (Math.abs(node.scrollTop - desired) < 2 || growths >= 8) restoring = false;
    };
    const scroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(remember);
    };
    const takeOver = () => { restoring = false; };
    // Retained Query rows restore immediately. A cold read may grow in bounded pages.
    restore();
    const observer = new MutationObserver(restore);
    observer.observe(node, { childList: true, subtree: true });
    const resize = new ResizeObserver(restore);
    resize.observe(node);
    const timeout = window.setTimeout(() => { restoring = false; observer.disconnect(); resize.disconnect(); }, 5_000);
    node.addEventListener("scroll", scroll, { passive: true });
    node.addEventListener("wheel", takeOver, { passive: true });
    node.addEventListener("touchstart", takeOver, { passive: true });
    node.addEventListener("pointerdown", takeOver);
    node.addEventListener("keydown", takeOver);
    const hide = () => { if (document.visibilityState === "hidden") remember(); };
    document.addEventListener("visibilitychange", hide);
    window.addEventListener("pagehide", remember);
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(timeout);
      observer.disconnect(); resize.disconnect();
      remember();
      node.removeEventListener("scroll", scroll);
      node.removeEventListener("wheel", takeOver);
      node.removeEventListener("touchstart", takeOver);
      node.removeEventListener("pointerdown", takeOver);
      node.removeEventListener("keydown", takeOver);
      document.removeEventListener("visibilitychange", hide);
      window.removeEventListener("pagehide", remember);
    };
  }, [principal, region, node, view]);
  return ref;
}
