import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Check, ChevronDown } from "lucide-react"

import type { ModelOption } from "@/lib/api"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import {
  formatEffort,
  formatModelSelection,
} from "@/features/agents/lib/provider/useModelOptions"
import { formatTokenCount } from "@/features/agents/lib/contextUsage"
import { Z } from "@/features/agents/components/z-index"
import { cn } from "@/lib/utils"

export interface ModelPickerProps {
  models: Array<ModelOption>
  selection: ModelSelection | null
  onSelectionChange?: (next: ModelSelection) => void
  disabled?: boolean
  /** Disables models that cannot accept image input (used when images are attached). */
  requireImageSupport?: boolean
  className?: string
  triggerClassName?: string
  /** Controlled open state, so `/model` in the composer can raise the picker. */
  open?: boolean
  onOpenChange?: (next: boolean) => void
}

type Pane = "models" | "efforts"

function effortForModel(
  model: ModelOption,
  selection: ModelSelection | null
): string {
  if (selection && selection.modelId === model.id) {
    if (model.efforts.includes(selection.effort)) return selection.effort
  }
  return model.default_effort
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pt-2 pb-1 text-[11px] text-muted-foreground/60">
      {children}
    </div>
  )
}

function OptionRow({
  label,
  selected,
  disabled = false,
  focused = false,
  onClick,
  onMouseEnter,
}: {
  label: React.ReactNode
  selected: boolean
  disabled?: boolean
  focused?: boolean
  onClick?: () => void
  onMouseEnter?: () => void
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      disabled={disabled}
      onClick={onClick}
      onMouseEnter={onMouseEnter}
      className={cn(
        "flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] whitespace-nowrap transition-colors",
        selected
          ? "text-foreground"
          : "text-muted-foreground",
        focused && "bg-accent",
        disabled
          ? "cursor-default opacity-40"
          : "cursor-pointer hover:bg-accent"
      )}
    >
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {selected && (
        <Check className="size-3.5 shrink-0 text-muted-foreground/60" />
      )}
    </button>
  )
}

/** Two-pane model picker: searchable model list plus context/reasoning detail. */
export function ModelPicker({
  models,
  selection,
  onSelectionChange,
  disabled = false,
  requireImageSupport = false,
  className,
  triggerClassName,
  open: controlledOpen,
  onOpenChange,
}: ModelPickerProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false)
  const open = controlledOpen ?? uncontrolledOpen
  // Read through a ref so `setOpen` stays referentially stable for the
  // click-outside listener while still resolving updater functions correctly.
  const openRef = useRef(open)
  openRef.current = open
  const setOpen = useCallback(
    (next: boolean | ((value: boolean) => boolean)) => {
      const value = typeof next === "function" ? next(openRef.current) : next
      setUncontrolledOpen(value)
      onOpenChange?.(value)
    },
    [onOpenChange]
  )
  const [query, setQuery] = useState("")
  const [focusedModelId, setFocusedModelId] = useState<string | null>(null)
  const [pane, setPane] = useState<Pane>("models")
  const containerRef = useRef<HTMLDivElement>(null)

  const pickerDisabled = disabled || models.length === 0 || !onSelectionChange

  const filteredModels = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return models
    return models.filter(
      (model) =>
        model.label.toLowerCase().includes(q) ||
        model.id.toLowerCase().includes(q)
    )
  }, [models, query])

  const focusedModel =
    filteredModels.find((model) => model.id === focusedModelId) ??
    filteredModels.find((model) => model.id === selection?.modelId) ??
    filteredModels[0]

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [setOpen])

  const apply = useCallback(
    (next: ModelSelection, { close }: { close: boolean }) => {
      onSelectionChange?.(next)
      if (!close) return
      setOpen(false)
      setQuery("")
      setPane("models")
    },
    [onSelectionChange, setOpen]
  )

  const modelDisabled = useCallback(
    (model: ModelOption) => requireImageSupport && !model.supports_images,
    [requireImageSupport]
  )

  const selectModel = useCallback(
    (model: ModelOption) => {
      if (modelDisabled(model)) return
      apply(
        { modelId: model.id, effort: effortForModel(model, selection) },
        { close: true }
      )
    },
    [apply, modelDisabled, selection]
  )

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.preventDefault()
      setOpen(false)
      return
    }
    if (e.key === "ArrowRight" && focusedModel) {
      e.preventDefault()
      setPane("efforts")
      return
    }
    if (e.key === "ArrowLeft") {
      e.preventDefault()
      setPane("models")
      return
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault()
      const step = e.key === "ArrowDown" ? 1 : -1
      if (pane === "efforts" && focusedModel && !modelDisabled(focusedModel)) {
        const efforts = focusedModel.efforts
        const current = efforts.indexOf(effortForModel(focusedModel, selection))
        const next =
          efforts[Math.min(Math.max(current + step, 0), efforts.length - 1)]
        if (next) {
          apply({ modelId: focusedModel.id, effort: next }, { close: false })
        }
        return
      }
      if (filteredModels.length === 0) return
      const current = filteredModels.findIndex(
        (model) => model.id === focusedModel?.id
      )
      const nextIndex = Math.min(
        Math.max(current + step, 0),
        filteredModels.length - 1
      )
      setFocusedModelId(filteredModels[nextIndex]?.id ?? null)
      return
    }
    if (e.key === "Enter" && focusedModel) {
      e.preventDefault()
      selectModel(focusedModel)
    }
  }

  const focusedContextWindow =
    typeof focusedModel?.context_window === "number"
      ? focusedModel.context_window
      : null

  return (
    <div
      ref={containerRef}
      className={cn("relative min-w-0 shrink", className)}
    >
      <button
        type="button"
        disabled={pickerDisabled}
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={cn(
          "flex max-w-[220px] cursor-pointer items-center gap-0.5 text-[13px] text-muted-foreground transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-60",
          triggerClassName
        )}
      >
        <span className="truncate">
          {formatModelSelection(models, selection)}
        </span>
        {!pickerDisabled && (
          <ChevronDown className="size-3.5 shrink-0 opacity-60" />
        )}
      </button>
      {open && !pickerDisabled && (
        <div
          data-testid="model-picker-panel"
          onKeyDown={handleKeyDown}
          style={{ zIndex: Z.DROPDOWN }}
          className="dropdown-glass absolute bottom-full left-0 mb-1 flex overflow-hidden rounded-xl"
        >
          <div className="flex w-60 flex-col">
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search models"
              aria-label="Search models"
              className="w-full border-b border-border bg-transparent px-3 py-2 text-[13px] text-foreground outline-none placeholder:text-muted-foreground/60"
            />
            <div
              role="listbox"
              aria-label="Models"
              className="max-h-72 overflow-y-auto py-1"
            >
              {filteredModels.length === 0 ? (
                <p className="px-3 py-1.5 text-[13px] text-muted-foreground/60">
                  No matches
                </p>
              ) : (
                filteredModels.map((model) => (
                  <OptionRow
                    key={model.id}
                    selected={selection?.modelId === model.id}
                    focused={focusedModel?.id === model.id}
                    disabled={modelDisabled(model)}
                    onMouseEnter={() => {
                      setFocusedModelId(model.id)
                      setPane("models")
                    }}
                    onClick={() => selectModel(model)}
                    label={
                      <>
                        {model.label}{" "}
                        <span className="text-muted-foreground/60">
                          {formatEffort(effortForModel(model, selection))}
                        </span>
                      </>
                    }
                  />
                ))
              )}
            </div>
          </div>
          {focusedModel && (
            <div className="flex w-52 flex-col border-l border-border py-1">
              {focusedContextWindow != null && (
                <>
                  <SectionHeading>Context</SectionHeading>
                  <div
                    className="flex items-center gap-2 px-3 py-1.5 text-[13px] text-foreground"
                    title="Context window reported for this model"
                  >
                    <span className="min-w-0 flex-1 truncate">
                      {formatTokenCount(focusedContextWindow)}
                    </span>
                    <Check className="size-3.5 shrink-0 text-muted-foreground/60" />
                  </div>
                </>
              )}
              <SectionHeading>Reasoning</SectionHeading>
              <div
                role="listbox"
                aria-label="Reasoning effort"
                className="max-h-60 overflow-y-auto"
              >
                {focusedModel.efforts.map((effort) => (
                  <OptionRow
                    key={effort}
                    label={formatEffort(effort)}
                    selected={
                      selection?.modelId === focusedModel.id &&
                      selection.effort === effort
                    }
                    disabled={modelDisabled(focusedModel)}
                    onMouseEnter={() => setPane("efforts")}
                    onClick={() =>
                      apply(
                        { modelId: focusedModel.id, effort },
                        { close: true }
                      )
                    }
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
