import { useEffect, useRef, useState } from "react";

import { formatElapsed } from "@/lib/utils";

const BUSY_TEXTS: Array<{ present: string; past: string }> = [
  { present: "Vibing...", past: "Vibed" },
  { present: "Noodling...", past: "Noodled" },
  { present: "Pondering...", past: "Pondered" },
  { present: "Thinking really hard...", past: "Thought really hard" },
  { present: "Spinning up...", past: "Spun up" },
  { present: "Connecting the dots...", past: "Connected the dots" },
  { present: "Brewing ideas...", past: "Brewed ideas" },
  { present: "Cooking...", past: "Cooked" },
  { present: "Crunching...", past: "Crunched" },
  { present: "Scheming...", past: "Schemed" },
  { present: "Processing...", past: "Processed" },
];

const THINKING_SETTLE_MS = 300;

export function ThinkingSpinner({
  isActive,
  settingUpSandbox = false,
}: {
  isActive: boolean;
  settingUpSandbox?: boolean;
}) {
  const [textIdx, setTextIdx] = useState(0);
  const [done, setDone] = useState<{ past: string; elapsed: string } | null>(null);
  const [settledActive, setSettledActive] = useState(isActive);
  const startTimeRef = useRef(0);
  const sessionActiveRef = useRef(false);
  const textIdxRef = useRef(textIdx);
  const settingUpSandboxRef = useRef(settingUpSandbox);
  textIdxRef.current = textIdx;

  useEffect(() => {
    settingUpSandboxRef.current = settingUpSandbox;
  }, [settingUpSandbox]);

  useEffect(() => {
    if (isActive) {
      setSettledActive(true);
      return;
    }
    const id = window.setTimeout(() => setSettledActive(false), THINKING_SETTLE_MS);
    return () => window.clearTimeout(id);
  }, [isActive]);

  useEffect(() => {
    if (settledActive) {
      if (!sessionActiveRef.current) {
        sessionActiveRef.current = true;
        startTimeRef.current = Date.now();
        setTextIdx(Math.floor(Math.random() * BUSY_TEXTS.length));
        setDone(null);
      }
      return;
    }
    if (!sessionActiveRef.current) return;
    sessionActiveRef.current = false;
    setDone({
      past: settingUpSandboxRef.current
        ? "Set up sandbox"
        : BUSY_TEXTS[textIdxRef.current]?.past ?? "",
      elapsed: formatElapsed(Date.now() - startTimeRef.current),
    });
  }, [settledActive]);

  useEffect(() => {
    if (!settledActive || settingUpSandbox) return;
    const BUSY_TEXT_ROTATE_INTERVAL_MS = 12000;
    const id = setInterval(() => setTextIdx((i) => (i + 1) % BUSY_TEXTS.length), BUSY_TEXT_ROTATE_INTERVAL_MS);
    return () => clearInterval(id);
  }, [settledActive, settingUpSandbox]);

  const showActive = isActive || settledActive;
  if (!showActive && !done) return null;

  if (done && !showActive) {
    return (
      <div className="my-2 flex items-center gap-2">
        <span className="text-xs text-[color:var(--ui-text-dim)]">{done.past} for {done.elapsed}</span>
      </div>
    );
  }

  return (
    <div className="my-2 flex items-center gap-2">
      <span className="shimmer-text text-xs">
        {settingUpSandbox ? "Setting up sandbox..." : BUSY_TEXTS[textIdx]?.present ?? ""}
      </span>
    </div>
  );
}
