"use client";

/*
 * Open product-rail width, remembered across reloads.
 *
 * Same external-store shape as `app-rail-collapsed.ts`. Collapse stays a
 * separate 48px icon rail; this store only covers the open column.
 */

import { useSyncExternalStore } from "react";

const STORAGE_KEY = "gtm-app-rail-width";

/** Designed Primary Sidebar on every surface board (`w-61.5`). */
const RAIL_WIDTH_DEFAULT = 246;
const RAIL_WIDTH_MIN = 246;
const RAIL_WIDTH_MAX = 400;
/** Icon rail when `railCollapsed`. Same number Tailwind's `w-12` used to own. */
const RAIL_WIDTH_COLLAPSED = 48;

const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", listener);

  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function clampRailWidth(value: number): number {
  if (!Number.isFinite(value)) return RAIL_WIDTH_DEFAULT;
  return Math.min(RAIL_WIDTH_MAX, Math.max(RAIL_WIDTH_MIN, Math.round(value)));
}

function readStoredWidth(): number {
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw === null) return RAIL_WIDTH_DEFAULT;
  return clampRailWidth(Number(raw));
}

function getSnapshot(): number {
  return readStoredWidth();
}

function getServerSnapshot(): number {
  return RAIL_WIDTH_DEFAULT;
}

function setAppRailWidth(next: number): void {
  window.localStorage.setItem(STORAGE_KEY, String(clampRailWidth(next)));
  for (const listener of listeners) {
    listener();
  }
}

function useAppRailWidth(): number {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export {
  RAIL_WIDTH_COLLAPSED,
  RAIL_WIDTH_DEFAULT,
  RAIL_WIDTH_MAX,
  RAIL_WIDTH_MIN,
  clampRailWidth,
  setAppRailWidth,
  useAppRailWidth,
};
