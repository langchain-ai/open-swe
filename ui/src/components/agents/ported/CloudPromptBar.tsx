import { ArrowUpIcon } from "@phosphor-icons/react";
import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

const MODELS = ["GPT-5.5 High", "Opus 4.6 High", "Composer 2.5 Fast"];
const PROMPT_TEXTAREA_MAX_HEIGHT = 200;

export interface CloudPromptBarProps {
  placeholder?: string;
  compact?: boolean;
  disabled?: boolean;
  busy?: boolean;
  onSubmit?: (value: string) => void;
}

/** Web-adapted PromptBar from open-swe-app — local state, no Electron/Zustand deps. */
export const CloudPromptBar = memo(function CloudPromptBar({
  placeholder = "Ask Open SWE to build, fix bugs, explore",
  compact = false,
  disabled = false,
  busy = false,
  onSubmit,
}: CloudPromptBarProps) {
  const [value, setValue] = useState("");
  const [model, setModel] = useState(MODELS[0]);
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false);
  const [modelDropdownIndex, setModelDropdownIndex] = useState(0);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const modelDropdownRef = useRef<HTMLDivElement>(null);

  const handleSubmit = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSubmit?.(trimmed);
    setValue("");
  }, [disabled, onSubmit, value]);

  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;

    el.style.height = "auto";
    const clampedHeight = Math.min(el.scrollHeight, PROMPT_TEXTAREA_MAX_HEIGHT);
    el.style.height = `${clampedHeight}px`;
    el.style.overflowY = el.scrollHeight > PROMPT_TEXTAREA_MAX_HEIGHT ? "auto" : "hidden";
  }, [value]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (modelDropdownRef.current && !modelDropdownRef.current.contains(e.target as Node)) {
        setModelDropdownOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && value.trim()) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className={cn("relative w-full font-sans text-[13px]", compact ? "max-w-none" : "max-w-2xl")}>
      <div
        className={cn(
          "relative flex flex-col rounded-2xl border border-[var(--ui-border)] bg-[var(--ui-surface)] shadow-sm",
          compact ? "min-h-[88px] px-4 pt-3.5 pb-2.5" : "min-h-[120px] px-4 py-3.5",
        )}
      >
        <textarea
          ref={inputRef}
          rows={1}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={busy ? "Send a message to queue next..." : placeholder}
          disabled={disabled}
          className={cn(
            "w-full min-w-0 resize-none overflow-hidden bg-transparent leading-[1.45] text-[color:var(--ui-text)] outline-none placeholder:text-[color:var(--ui-text-dim)]",
            compact ? "min-h-[44px]" : "min-h-[64px]",
          )}
          style={{ maxHeight: PROMPT_TEXTAREA_MAX_HEIGHT }}
        />

        <div
          className={cn(
            "flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0 text-xs text-[color:var(--ui-text-dim)]",
            compact ? "pt-1.5" : "mt-auto pt-2",
          )}
        >
          <div ref={modelDropdownRef} className="relative min-w-0 shrink">
            <button
              type="button"
              onClick={() => {
                setModelDropdownOpen((open) => !open);
                setModelDropdownIndex(0);
              }}
              className="max-w-[180px] cursor-pointer truncate text-[color:var(--ui-text-muted)] transition-opacity hover:opacity-80"
            >
              {model}
            </button>
            {modelDropdownOpen && (
              <div className="absolute bottom-full left-0 z-50 mb-1 overflow-hidden rounded border border-[var(--ui-border)] bg-[var(--ui-surface)] shadow-lg">
                {MODELS.map((option, idx) => {
                  const selected = option === model;
                  return (
                    <button
                      key={option}
                      type="button"
                      onClick={() => {
                        setModel(option);
                        setModelDropdownOpen(false);
                      }}
                      onMouseEnter={() => setModelDropdownIndex(idx)}
                      className={cn(
                        "flex w-full items-center gap-2 whitespace-nowrap px-3 py-1.5 text-left transition-colors",
                        idx === modelDropdownIndex
                          ? "bg-[var(--ui-panel-2)]"
                          : "hover:bg-[var(--ui-panel-2)]",
                        selected ? "text-[color:var(--ui-text)]" : "text-[color:var(--ui-text-muted)]",
                      )}
                    >
                      {option}
                      {selected && <span className="ml-auto pl-3 text-[color:var(--ui-text-dim)]">✓</span>}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <span className="ml-auto" />

          <button
            type="button"
            onClick={handleSubmit}
            disabled={disabled || !value.trim()}
            className="flex size-8 shrink-0 items-center justify-center rounded-full bg-[#87CEEB] text-slate-700 hover:brightness-95 disabled:opacity-50"
            aria-label="Send"
          >
            <ArrowUpIcon className="size-4" weight="bold" />
          </button>
        </div>
      </div>
    </div>
  );
});
