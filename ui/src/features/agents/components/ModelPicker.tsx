import {
  CaretDownIcon,
  CheckIcon,
  MagnifyingGlassRegularIcon,
} from "@langchain/macaw-components/icons"
import {
  useCallback,
  useEffect,
  useEffectEvent,
  useMemo,
  useRef,
  useState,
} from "react"
import { Input } from "@langchain/macaw-components/Input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"

import type { ModelOption } from "@/lib/api"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import {
  formatEffort,
  formatModelSelection,
  routedModelLabel,
} from "@/features/agents/lib/provider/useModelOptions"
import { formatTokenCount } from "@/features/agents/lib/contextUsage"
import { cn } from "@/lib/utils"

export interface ModelPickerProps {
  models: Array<ModelOption>
  selection: ModelSelection | null
  onSelectionChange?: (next: ModelSelection | null) => void
  disabled?: boolean
  /** Disables models that cannot accept image input (used when images are attached). */
  requireImageSupport?: boolean
  className?: string
  triggerClassName?: string
  /** Controlled open state, so `/model` in the composer can raise the picker. */
  open?: boolean
  onOpenChange?: (next: boolean) => void
  /** Route/model the Auto router picked for the current run. */
  routed?: { route?: string; modelId?: string | null } | null
}

const AUTO_ID = "__auto__"

const PANE =
  "rounded-lg border border-default bg-elevated p-0 text-primary shadow-md outline-none"

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
    <div className="px-space-3 pt-space-2 pb-space-1 text-xxs text-tertiary">
      {children}
    </div>
  )
}

function OptionRow({
  label,
  selected,
  disabled = false,
  focused = false,
  trailing,
  onClick,
  onMouseEnter,
}: {
  label: React.ReactNode
  selected: boolean
  disabled?: boolean
  focused?: boolean
  trailing?: React.ReactNode
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
        "flex w-full items-center gap-space-2 px-space-3 py-space-1 text-left text-xs whitespace-nowrap transition-colors",
        selected ? "text-primary" : "text-secondary",
        focused && "bg-elevated-hover",
        disabled
          ? "cursor-default opacity-40"
          : "cursor-pointer hover:bg-elevated-hover"
      )}
    >
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {trailing ??
        (selected && (
          <CheckIcon
            size={14}
            weight="regular"
            className="shrink-0 text-icon-tertiary"
          />
        ))}
    </button>
  )
}

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
  routed,
}: ModelPickerProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false)
  const open = controlledOpen ?? uncontrolledOpen
  // Read through a ref so `setOpen` stays referentially stable while still
  // resolving updater functions correctly.
  const openRef = useRef(open)
  useEffect(() => {
    openRef.current = open
  }, [open])
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

  const pickerDisabled = disabled || models.length === 0 || !onSelectionChange

  const selectedModel = models.find((model) => model.id === selection?.modelId)
  const selectedEntryId = selectedModel?.id ?? AUTO_ID
  const efforts = selectedModel?.efforts ?? []
  const currentEffort = selectedModel
    ? effortForModel(selectedModel, selection)
    : null

  const filteredModels = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return models
    return models.filter(
      (model) =>
        model.label.toLowerCase().includes(q) ||
        model.id.toLowerCase().includes(q)
    )
  }, [models, query])

  const showAuto = !query.trim()
  const entryIds = useMemo(
    () => [
      ...(showAuto ? [AUTO_ID] : []),
      ...filteredModels.map((model) => model.id),
    ],
    [filteredModels, showAuto]
  )
  const focusedEntryId =
    [focusedModelId, selectedEntryId].find(
      (id): id is string => id != null && entryIds.includes(id)
    ) ?? entryIds[0]

  // Seeded once per open; later edits come from pointer/keyboard interaction.
  const seedOnOpen = useEffectEvent(() => {
    setQuery("")
    setFocusedModelId(null)
  })
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    if (open) seedOnOpen()
  }, [open])

  const close = useCallback(() => {
    setOpen(false)
    setQuery("")
  }, [setOpen])

  const modelDisabled = useCallback(
    (model: ModelOption) => requireImageSupport && !model.supports_images,
    [requireImageSupport]
  )

  const applyEffort = (effort: string) => {
    if (!selectedModel) return
    onSelectionChange?.({ modelId: selectedModel.id, effort })
    close()
  }

  const selectModel = useCallback(
    (model: ModelOption) => {
      if (modelDisabled(model)) return
      onSelectionChange?.({
        modelId: model.id,
        effort: effortForModel(model, selection),
      })
      close()
    },
    [close, modelDisabled, onSelectionChange, selection]
  )

  const selectAuto = useCallback(() => {
    onSelectionChange?.(null)
    close()
  }, [close, onSelectionChange])

  const selectEntry = useCallback(
    (id: string) => {
      if (id === AUTO_ID) {
        selectAuto()
        return
      }
      const model = filteredModels.find((entry) => entry.id === id)
      if (model) selectModel(model)
    },
    [filteredModels, selectAuto, selectModel]
  )

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.preventDefault()
      close()
      return
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault()
      const availableIds = entryIds.filter((id) => {
        const model = models.find((entry) => entry.id === id)
        return !model || !modelDisabled(model)
      })
      const step = e.key === "ArrowDown" ? 1 : -1
      const current = availableIds.indexOf(focusedEntryId ?? "")
      const nextIndex = Math.min(
        Math.max(current + step, 0),
        availableIds.length - 1
      )
      setFocusedModelId(availableIds[nextIndex] ?? null)
    }
    if (e.key === "Enter" && focusedEntryId) {
      e.preventDefault()
      selectEntry(focusedEntryId)
    }
  }

  const contextWindow =
    typeof selectedModel?.context_window === "number"
      ? selectedModel.context_window
      : null
  const routedLabel = routedModelLabel(models, routed)
  const triggerLabel = formatModelSelection(models, selection, routed)
  const triggerTitle =
    !selection && routedLabel ? `Routed model: ${routedLabel}` : undefined

  return (
    <div className={cn("relative min-w-0 shrink", className)}>
      <Popover open={open && !pickerDisabled} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            disabled={pickerDisabled}
            aria-haspopup="listbox"
            title={triggerTitle}
            className={cn(
              "flex max-w-[220px] cursor-pointer items-center gap-0.5 text-xs text-secondary transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-60",
              triggerClassName
            )}
          >
            <span className="truncate">{triggerLabel}</span>
            {!pickerDisabled && (
              <CaretDownIcon
                size={14}
                weight="regular"
                className="shrink-0 opacity-60"
              />
            )}
          </button>
        </PopoverTrigger>
        <PopoverContent
          data-testid="model-picker-panel"
          side="top"
          align="start"
          sideOffset={4}
          className={cn(PANE, "w-60 overflow-hidden py-space-1")}
        >
          <div onKeyDown={handleKeyDown}>
            <Input
              autoFocus
              variant="plain"
              size="md"
              leftIcon={MagnifyingGlassRegularIcon}
              value={query}
              onChange={setQuery}
              placeholder="Search models"
              aria-label="Search models"
              inputContainerClassName="border-b border-default"
            />
            <div
              role="listbox"
              aria-label="Models"
              className="max-h-72 overflow-y-auto py-space-1"
            >
              {showAuto && (
                <OptionRow
                  label="Auto"
                  selected={!selectedModel}
                  focused={focusedEntryId === AUTO_ID}
                  onMouseEnter={() => setFocusedModelId(AUTO_ID)}
                  onClick={selectAuto}
                />
              )}
              {filteredModels.length === 0 ? (
                <p className="px-space-3 py-space-1 text-xs text-tertiary">
                  No matches
                </p>
              ) : (
                filteredModels.map((model) => (
                  <OptionRow
                    key={model.id}
                    selected={selectedModel?.id === model.id}
                    focused={focusedEntryId === model.id}
                    disabled={modelDisabled(model)}
                    onMouseEnter={() => setFocusedModelId(model.id)}
                    onClick={() => selectModel(model)}
                    label={
                      <>
                        {model.label}{" "}
                        <span className="text-tertiary">
                          {formatEffort(effortForModel(model, selection))}
                        </span>
                      </>
                    }
                  />
                ))
              )}
            </div>
          </div>
          {selectedModel ? (
            <>
              {contextWindow != null && (
                <>
                  <SectionHeading>Context</SectionHeading>
                  <div
                    className="flex items-center gap-space-2 px-space-3 py-space-1 text-xs text-primary"
                    title="Context window reported for this model"
                  >
                    <span className="min-w-0 flex-1 truncate">
                      {formatTokenCount(contextWindow)}
                    </span>
                  </div>
                </>
              )}
              <SectionHeading>Reasoning</SectionHeading>
              <div
                role="listbox"
                aria-label="Reasoning effort"
                className="max-h-60 overflow-y-auto"
              >
                {efforts.map((effort) => (
                  <OptionRow
                    key={effort}
                    label={formatEffort(effort)}
                    selected={currentEffort === effort}
                    onClick={() => applyEffort(effort)}
                  />
                ))}
              </div>
            </>
          ) : (
            <p className="px-space-3 py-space-1 text-xs text-tertiary">
              Model and reasoning are chosen when the thread starts.
            </p>
          )}
        </PopoverContent>
      </Popover>
    </div>
  )
}
