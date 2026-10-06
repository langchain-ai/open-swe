"use client";

import { createContext, useCallback, useContext, useMemo, useState, useSyncExternalStore } from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";
import { toast } from "sonner";
import { z } from "zod";

/** Local working copies are never authorization, server truth, or queued actions. */
const PREFIX = "gtm.recovery.v1:";
const MAX_ENTRY_BYTES = 600_000;
const MAX_ENTRIES = 100;
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const PREFERENCE_AGE_MS = 180 * 24 * 60 * 60 * 1000;
type RecoveryStorage = "session" | "local";
const PrincipalContext = createContext<string | null>(null);
const PathContext = createContext("");
const listeners = new Set<() => void>();
const memory = new Map<string, string>();
const failed = new Set<string>();
const envelopeSchema = z.object({ savedAt: z.number().finite(), value: z.unknown() });

export const recoveryText = z.string().max(200_000);
export const recoveryId = z.string().max(512);
export const recoveryStringMap = z.record(z.string().max(512), recoveryText);

export function RecoveryScope({ principal, pathname = "", children }: { principal: string | null; pathname?: string; children: ReactNode }) {
  return <PrincipalContext value={principal}><PathContext value={pathname}>{children}</PathContext></PrincipalContext>;
}

export function useRecoveryPathname(): string { return useContext(PathContext); }

export function useRecoveryView(): string {
  return useSyncExternalStore(subscribe, () => `${window.location.pathname}${window.location.search}`, () => "");
}

export function useRecoveryFailure(): boolean {
  return useSyncExternalStore(subscribe, hasUnstoredRecovery, () => false);
}

export function useRecoveryPrincipal(): string | null {
  return useContext(PrincipalContext);
}

function storage(kind: RecoveryStorage): Storage {
  return kind === "local" ? window.localStorage : window.sessionStorage;
}

export function recoveryStorageKey(principal: string, key: string): string {
  return `${PREFIX}${encodeURIComponent(principal)}:${encodeURIComponent(key)}`;
}

function publish(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function safeKeys(value: unknown): boolean {
  if (Array.isArray(value)) return value.every(safeKeys);
  if (value === null || typeof value !== "object") return true;
  return Object.entries(value).every(([key, item]) =>
    key !== "__proto__" && key !== "constructor" && key !== "prototype" && safeKeys(item)
  );
}

export function readRecovery<T>(principal: string, key: string, schema: z.ZodType<T>, kind: RecoveryStorage = "session"): T | undefined {
  const raw = readRaw(recoveryStorageKey(principal, key), kind);
  return decode(raw, schema, kind, memory.has(`${kind}:${recoveryStorageKey(principal, key)}`));
}

function readRaw(key: string | null, kind: RecoveryStorage): string | null {
  if (key === null || typeof window === "undefined") return null;
  const held = memory.get(`${kind}:${key}`);
  if (held !== undefined) return held;
  try {
    return storage(kind).getItem(key);
  } catch {
    return null;
  }
}

function decode<T>(raw: string | null, schema: z.ZodType<T>, kind: RecoveryStorage, live = false): T | undefined {
  if (raw === null || (!live && raw.length * 2 > MAX_ENTRY_BYTES)) return undefined;
  try {
    const envelope = envelopeSchema.safeParse(JSON.parse(raw));
    if (!envelope.success || !safeKeys(envelope.data.value)) return undefined;
    const age = Date.now() - envelope.data.savedAt;
    if (age < 0 || age > (kind === "local" ? PREFERENCE_AGE_MS : MAX_AGE_MS)) return undefined;
    const parsed = schema.safeParse(envelope.data.value);
    return parsed.success ? parsed.data : undefined;
  } catch {
    return undefined;
  }
}

export function writeRecovery<T>(principal: string, key: string, value: T, kind: RecoveryStorage = "session"): void {
  writeRaw(recoveryStorageKey(principal, key), value, kind);
}

/** Whether a stored envelope already holds this value, compared by content rather than identity. */
function sameStoredValue(raw: string, value: unknown): boolean {
  try {
    const envelope = envelopeSchema.safeParse(JSON.parse(raw));
    return envelope.success && JSON.stringify(envelope.data.value) === JSON.stringify(value);
  } catch {
    return false;
  }
}

function writeRaw(key: string, value: unknown, kind: RecoveryStorage): void {
  const raw = JSON.stringify({ savedAt: Date.now(), value });
  const memoryKey = `${kind}:${key}`;
  // Keep the live edit even if storage is full. Never evict another unsaved draft.
  memory.set(memoryKey, raw);
  try {
    const target = storage(kind);
    for (const item of Object.keys(target)) {
      if (!item.startsWith(PREFIX)) continue;
      try {
        const saved = envelopeSchema.safeParse(JSON.parse(target.getItem(item) ?? "null"));
        if (saved.success && Date.now() - saved.data.savedAt > (kind === "local" ? PREFERENCE_AGE_MS : MAX_AGE_MS)) target.removeItem(item);
      } catch { /* A corrupt entry is ignored on read. */ }
    }
    const count = Object.keys(target).filter((item) => item.startsWith(PREFIX)).length;
    if (raw.length * 2 > MAX_ENTRY_BYTES || (count >= MAX_ENTRIES && target.getItem(key) === null)) {
      throw new Error("Recovery capacity reached");
    }
    target.setItem(key, raw);
    failed.delete(memoryKey);
    // Read successful preference writes from storage so other tabs can update them.
    memory.delete(memoryKey);
  } catch {
    failed.add(memoryKey);
    toast.error("Your latest changes could not be saved on this device. Keep this page open until you save them.", { id: "recovery-unavailable" });
  }
  publish();
}

export function removeRecovery(principal: string, key: string, kind: RecoveryStorage = "session"): void {
  const storageKey = recoveryStorageKey(principal, key);
  memory.delete(`${kind}:${storageKey}`);
  failed.delete(`${kind}:${storageKey}`);
  try { storage(kind).removeItem(storageKey); } catch { /* Storage can be disabled. */ }
  publish();
}

/** Signout clears private tab copies; persistent preferences contain presentation only. */
export function clearBrowserRecovery(): void {
  memory.clear();
  failed.clear();
  if (typeof window !== "undefined") {
    try {
      for (const key of Object.keys(window.sessionStorage)) {
        if (key.startsWith(PREFIX) || key.startsWith("gtm.playWorkingCopy.v1:")) window.sessionStorage.removeItem(key);
      }
    } catch { /* The session may have disabled storage. */ }
  }
  publish();
}

export function setRecoveryPending(key: string, pending: boolean): void {
  if (pending) failed.add(key);
  else failed.delete(key);
  publish();
}

export function hasUnstoredRecovery(): boolean { return failed.size > 0; }

/** Explicit schemas keep browser data out of product types until it is validated. */
export function useRecoveryState<T>(
  key: string | null,
  initial: T | (() => T),
  schema: z.ZodType<T>,
  kind: RecoveryStorage = "session"
): [T, Dispatch<SetStateAction<T>>, () => void] {
  const principal = useRecoveryPrincipal();
  const storageKey = principal === null || key === null ? null : recoveryStorageKey(principal, key);
  const stateKey = storageKey ?? key;
  const [local, setLocal] = useState(() => ({ key: stateKey, value: typeof initial === "function" ? (initial as () => T)() : initial }));
  const fallback = local.key === stateKey ? local.value : typeof initial === "function" ? (initial as () => T)() : initial;
  if (local.key !== stateKey) setLocal({ key: stateKey, value: fallback });
  const getSnapshot = useCallback(() => readRaw(storageKey, kind), [storageKey, kind]);
  const raw = useSyncExternalStore(subscribe, getSnapshot, () => null);
  const value = useMemo(() => decode(raw, schema, kind, memory.has(`${kind}:${storageKey}`)) ?? fallback, [raw, schema, kind, fallback, storageKey]);
  const setValue: Dispatch<SetStateAction<T>> = useCallback((update) => {
    if (storageKey === null) {
      setLocal((previous) => ({ key: stateKey, value: typeof update === "function" ? (update as (previous: T) => T)(previous.value) : update }));
      return;
    }
    const held = readRaw(storageKey, kind);
    const current = decode(held, schema, kind, memory.has(`${kind}:${storageKey}`)) ?? fallback;
    const next = typeof update === "function" ? (update as (previous: T) => T)(current) : update;
    if (Object.is(next, current)) return;
    // Validate the value being written as well as the restored value.
    const parsed = schema.safeParse(next);
    if (!parsed.success || !safeKeys(next)) return;
    // A write that changes nothing must change nothing: a table engine resets its page index as a
    // queued microtask whenever its rows or sort order change identity, and a store that republished
    // an equal value with fresh identities fed that reset forever, starving the event loop. A value
    // held only in memory after storage refused it is not stored, so an equal retry still writes.
    if (held !== null && !failed.has(`${kind}:${storageKey}`) && sameStoredValue(held, parsed.data)) return;
    writeRaw(storageKey, parsed.data, kind);
  }, [storageKey, stateKey, schema, kind, fallback]);
  const clear = () => {
    if (principal !== null && key !== null) removeRecovery(principal, key, kind);
    setLocal({ key: stateKey, value: typeof initial === "function" ? (initial as () => T)() : initial });
  };
  return [value, setValue, clear];
}
