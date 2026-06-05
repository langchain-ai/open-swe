import { ArrowUp, ChevronDown, LoaderCircle, Square } from "lucide-react"
import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"

import type { ModelOption } from "@/lib/api"
import type { ModelSelection } from "@/lib/agents/useModelOptions"
import { RepoSelector } from "@/components/agents/RepoSelector"
import { formatModelSelection } from "@/lib/agents/useModelOptions"
import { cn } from "@/lib/utils"

const PROMPT_TEXTAREA_MAX_HEIGHT = 200

export interface CloudPromptBarProps {
  placeholder?: string
  compact?: boolean
  disabled?: boolean
  busy?: boolean
  onSubmit?: (value: string) => void
  /** Called to stop the running agent. When set, the send button becomes a stop button while busy and the input is empty. */
  onStop?: () => void
  stopping?: boolean
  models?: Array<ModelOption>
  selection?: ModelSelection | null
  onSelectionChange?: (next: ModelSelection) => void
  /** Repos the user can target. When provided with onRepoChange, a repo picker is shown. */
  repos?: Array<{ full_name: string }>
  selectedRepo?: string | null
  onRepoChange?: (repo: string | null) => void
}

/** Web-adapted PromptBar from open-swe-app — local state, no Electron/Zustand deps. */
export const CloudPromptBar = memo(function CloudPromptBarComponent({
  placeholder = "Ask Open SWE to build, fix bugs, explore",
  compact = false,
  disabled = false,
  busy = false,
  onSubmit,
  onStop,
  stopping = false,
  models = [],
  selection = null,
  onSelectionChange,
  repos,
  selectedRepo = null,
  onRepoChange,
}: CloudPromptBarProps) {
  const [value, setValue] = useState("")
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const modelDropdownRef = useRef<HTMLDivElement>(null)

  const combos = useMemo<Array<ModelSelection>>(() => {
    const list: Array<ModelSelection> = []
    for (const model of models) {
      for (const effort of model.efforts) {
        list.push({ modelId: model.id, effort })
      }
    }
    return list
  }, [models])

  const selectionLabel = formatModelSelection(models, selection)

  const handleSubmit = useCallback(() => {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    onSubmit?.(trimmed)
    setValue("")
  }, [disabled, onSubmit, value])

  useLayoutEffect(() => {
    const el = inputRef.current
    if (!el) return

    el.style.height = "auto"
    const clampedHeight = Math.min(el.scrollHeight, PROMPT_TEXTAREA_MAX_HEIGHT)
    el.style.height = `${clampedHeight}px`
    el.style.overflowY =
      el.scrollHeight > PROMPT_TEXTAREA_MAX_HEIGHT ? "auto" : "hidden"
  }, [value])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      const target = e.target as Node
      if (
        modelDropdownRef.current &&
        !modelDropdownRef.current.contains(target)
      ) {
        setModelDropdownOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && value.trim()) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const pickerDisabled = combos.length === 0 || !onSelectionChange
  const showStop = busy && !value.trim() && !!onStop

  return (
    <div
      className={cn(
        "relative w-full font-sans text-[13px]",
        compact ? "max-w-none" : "max-w-2xl"
      )}
    >
      {onRepoChange && (
        <div className="mb-2 flex items-center gap-2 px-1 text-xs">
          <RepoSelector
            repos={repos}
            selectedRepo={selectedRepo}
            onRepoChange={onRepoChange}
          />
        </div>
      )}
      <div
        className={cn(
          "relative flex min-h-[106px] flex-col rounded-2xl border border-[var(--ui-border)] bg-[var(--ui-surface)] px-3 py-2.5 shadow-sm",
          compact && "min-h-[88px]"
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
            "w-full min-w-0 resize-none overflow-hidden bg-transparent text-[13px] leading-[1.45] text-[color:var(--ui-text)] outline-none placeholder:text-[color:var(--ui-text-dim)]",
            compact ? "min-h-[36px]" : "min-h-[52px]"
          )}
          style={{ maxHeight: PROMPT_TEXTAREA_MAX_HEIGHT }}
        />

        <div className="mt-auto flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 pt-2 text-xs text-[color:var(--ui-text-dim)]">
          <div ref={modelDropdownRef} className="relative min-w-0 shrink">
            <button
              type="button"
              disabled={pickerDisabled}
              onClick={() => setModelDropdownOpen((open) => !open)}
              className="flex max-w-[220px] cursor-pointer items-center gap-0.5 text-[13px] text-[color:var(--ui-text-muted)] transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-60"
            >
              <span className="truncate">{selectionLabel}</span>
              {!pickerDisabled && (
                <ChevronDown className="size-3.5 shrink-0 opacity-60" />
              )}
            </button>
            {modelDropdownOpen && combos.length > 0 && (
              <div className="absolute bottom-full left-0 z-50 mb-1 max-h-72 overflow-hidden overflow-y-auto rounded border border-[var(--ui-border)] bg-[var(--ui-surface)] shadow-lg">
                {combos.map((combo) => {
                  const selected =
                    !!selection &&
                    selection.modelId === combo.modelId &&
                    selection.effort === combo.effort
                  return (
                    <button
                      key={`${combo.modelId}::${combo.effort}`}
                      type="button"
                      onClick={() => {
                        onSelectionChange?.(combo)
                        setModelDropdownOpen(false)
                      }}
                      className={cn(
                        "flex w-full items-center gap-2 px-3 py-1.5 text-left whitespace-nowrap transition-colors hover:bg-[var(--ui-panel-2)]",
                        selected
                          ? "text-[color:var(--ui-text)]"
                          : "text-[color:var(--ui-text-muted)]"
                      )}
                    >
                      {formatModelSelection(models, combo)}
                      {selected && (
                        <span className="ml-auto pl-3 text-[color:var(--ui-text-dim)]">
                          ✓
                        </span>
                      )}
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          {showStop ? (
            <button
              type="button"
              onClick={onStop}
              disabled={stopping}
              aria-label="Stop run"
              className="ml-auto flex size-7 shrink-0 items-center justify-center rounded-full bg-[var(--ui-accent)] text-white transition-opacity hover:opacity-90 disabled:cursor-default disabled:opacity-40"
            >
              {stopping ? (
                <LoaderCircle className="size-3.5 animate-spin" />
              ) : (
                <Square className="size-3 fill-current" strokeWidth={0} />
              )}
            </button>
          ) : (
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!value.trim() || disabled}
              aria-label="Send message"
              className="ml-auto flex size-7 shrink-0 items-center justify-center rounded-full bg-[var(--ui-accent)] text-white transition-opacity hover:opacity-90 disabled:cursor-default disabled:opacity-40"
            >
              {disabled ? (
                <LoaderCircle className="size-3.5 animate-spin" />
              ) : (
                <ArrowUp className="size-3.5" strokeWidth={2.5} />
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  )
})
