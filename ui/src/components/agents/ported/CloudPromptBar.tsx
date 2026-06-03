import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { CaretDownIcon, FolderIcon } from "@phosphor-icons/react";

import type { ModelOption } from "@/lib/api";
import {
  formatModelSelection,
  type ModelSelection,
} from "@/lib/agents/useModelOptions";
import { cn } from "@/lib/utils";

const PROMPT_TEXTAREA_MAX_HEIGHT = 200;

export interface CloudPromptBarProps {
  placeholder?: string;
  compact?: boolean;
  disabled?: boolean;
  busy?: boolean;
  onSubmit?: (value: string) => void;
  models?: ModelOption[];
  selection?: ModelSelection | null;
  onSelectionChange?: (next: ModelSelection) => void;
  /** Repos the user can target. When provided with onRepoChange, a repo picker is shown. */
  repos?: Array<{ full_name: string }>;
  selectedRepo?: string | null;
  onRepoChange?: (repo: string | null) => void;
}

/** Web-adapted PromptBar from open-swe-app — local state, no Electron/Zustand deps. */
export const CloudPromptBar = memo(function CloudPromptBar({
  placeholder = "Ask Open SWE to build, fix bugs, explore",
  compact = false,
  disabled = false,
  busy = false,
  onSubmit,
  models = [],
  selection = null,
  onSelectionChange,
  repos,
  selectedRepo = null,
  onRepoChange,
}: CloudPromptBarProps) {
  const [value, setValue] = useState("");
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false);
  const [repoDropdownOpen, setRepoDropdownOpen] = useState(false);
  const [repoQuery, setRepoQuery] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const modelDropdownRef = useRef<HTMLDivElement>(null);
  const repoDropdownRef = useRef<HTMLDivElement>(null);

  const repoPickerEnabled = !!onRepoChange;
  const filteredRepos = useMemo(() => {
    const all = repos ?? [];
    const q = repoQuery.trim().toLowerCase();
    if (!q) return all;
    return all.filter((r) => r.full_name.toLowerCase().includes(q));
  }, [repos, repoQuery]);

  const combos = useMemo<ModelSelection[]>(() => {
    const list: ModelSelection[] = [];
    for (const model of models) {
      for (const effort of model.efforts) {
        list.push({ modelId: model.id, effort });
      }
    }
    return list;
  }, [models]);

  const selectionLabel = formatModelSelection(models, selection);

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
      const target = e.target as Node;
      if (modelDropdownRef.current && !modelDropdownRef.current.contains(target)) {
        setModelDropdownOpen(false);
      }
      if (repoDropdownRef.current && !repoDropdownRef.current.contains(target)) {
        setRepoDropdownOpen(false);
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

  const pickerDisabled = combos.length === 0 || !onSelectionChange;

  return (
    <div className={cn("relative w-full font-sans text-[13px]", compact ? "max-w-none" : "max-w-2xl")}>
      {repoPickerEnabled && (
        <div className="mb-2 flex items-center gap-2 px-1 text-xs">
          <div ref={repoDropdownRef} className="relative min-w-0 shrink">
            <button
              type="button"
              onClick={() => setRepoDropdownOpen((open) => !open)}
              className="flex max-w-[260px] cursor-pointer items-center gap-1 text-[color:var(--ui-text-muted)] transition-opacity hover:opacity-80"
            >
              <FolderIcon className="size-3.5 shrink-0" />
              <span className="truncate">{selectedRepo || "Select repository"}</span>
              <CaretDownIcon className="size-3 shrink-0 opacity-70" />
            </button>
            {repoDropdownOpen && (
              <div className="absolute left-0 top-full z-50 mt-1 flex max-h-72 w-72 flex-col overflow-hidden rounded border border-[var(--ui-border)] bg-[var(--ui-surface)] shadow-lg">
                <input
                  autoFocus
                  value={repoQuery}
                  onChange={(e) => setRepoQuery(e.target.value)}
                  placeholder="Search repositories…"
                  className="w-full border-b border-[var(--ui-border)] bg-transparent px-3 py-2 text-[color:var(--ui-text)] outline-none placeholder:text-[color:var(--ui-text-dim)]"
                />
                <div className="overflow-y-auto">
                  <button
                    type="button"
                    onClick={() => {
                      onRepoChange(null);
                      setRepoDropdownOpen(false);
                      setRepoQuery("");
                    }}
                    className={cn(
                      "flex w-full items-center px-3 py-1.5 text-left transition-colors hover:bg-[var(--ui-panel-2)]",
                      selectedRepo
                        ? "text-[color:var(--ui-text-muted)]"
                        : "text-[color:var(--ui-text)]",
                    )}
                  >
                    No repository
                    {!selectedRepo && (
                      <span className="ml-auto pl-3 text-[color:var(--ui-text-dim)]">✓</span>
                    )}
                  </button>
                  {filteredRepos.length === 0 ? (
                    <div className="px-3 py-1.5 text-[color:var(--ui-text-dim)]">No matches</div>
                  ) : (
                    filteredRepos.map((repo) => {
                      const selected = repo.full_name === selectedRepo;
                      return (
                        <button
                          key={repo.full_name}
                          type="button"
                          onClick={() => {
                            onRepoChange(repo.full_name);
                            setRepoDropdownOpen(false);
                            setRepoQuery("");
                          }}
                          className={cn(
                            "flex w-full items-center px-3 py-1.5 text-left transition-colors hover:bg-[var(--ui-panel-2)]",
                            selected
                              ? "text-[color:var(--ui-text)]"
                              : "text-[color:var(--ui-text-muted)]",
                          )}
                        >
                          <span className="truncate">{repo.full_name}</span>
                          {selected && (
                            <span className="ml-auto pl-3 text-[color:var(--ui-text-dim)]">✓</span>
                          )}
                        </button>
                      );
                    })
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
      <div
        className={cn(
          "relative flex min-h-[106px] flex-col rounded-2xl border border-[var(--ui-border)] bg-[var(--ui-surface)] px-4 py-3.5 shadow-sm",
          compact && "min-h-[88px]",
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
            compact ? "min-h-[36px]" : "min-h-[52px]",
          )}
          style={{ maxHeight: PROMPT_TEXTAREA_MAX_HEIGHT }}
        />

        <div className="mt-auto flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 pt-2 text-xs text-[color:var(--ui-text-dim)]">
          <div ref={modelDropdownRef} className="relative min-w-0 shrink">
            <button
              type="button"
              disabled={pickerDisabled}
              onClick={() => setModelDropdownOpen((open) => !open)}
              className="max-w-[220px] cursor-pointer truncate text-[color:var(--ui-text-muted)] transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-60"
            >
              {selectionLabel}
            </button>
            {modelDropdownOpen && combos.length > 0 && (
              <div className="absolute bottom-full left-0 z-50 mb-1 max-h-72 overflow-hidden overflow-y-auto rounded border border-[var(--ui-border)] bg-[var(--ui-surface)] shadow-lg">
                {combos.map((combo) => {
                  const selected =
                    !!selection &&
                    selection.modelId === combo.modelId &&
                    selection.effort === combo.effort;
                  return (
                    <button
                      key={`${combo.modelId}::${combo.effort}`}
                      type="button"
                      onClick={() => {
                        onSelectionChange?.(combo);
                        setModelDropdownOpen(false);
                      }}
                      className={cn(
                        "flex w-full items-center gap-2 whitespace-nowrap px-3 py-1.5 text-left transition-colors hover:bg-[var(--ui-panel-2)]",
                        selected
                          ? "text-[color:var(--ui-text)]"
                          : "text-[color:var(--ui-text-muted)]",
                      )}
                    >
                      {formatModelSelection(models, combo)}
                      {selected && (
                        <span className="ml-auto pl-3 text-[color:var(--ui-text-dim)]">✓</span>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
});
