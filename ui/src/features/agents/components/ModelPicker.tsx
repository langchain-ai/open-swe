import { Select } from "@langchain/macaw-components/Select"
import { SparkleIcon } from "@phosphor-icons/react/dist/ssr/Sparkle"

import { ModelSelect } from "@/components/ModelSelect"
import {
  formatAutoModelSelection,
  formatEffort,
  type ModelSelection,
} from "@/features/agents/lib/provider/useModelOptions"
import type { ModelOption } from "@/lib/api"

interface ModelPickerProps {
  models: ModelOption[]
  selection: ModelSelection | null
  onSelectionChange?: (next: ModelSelection | null) => void
  disabled?: boolean
  requireImageSupport?: boolean
  open?: boolean
  onOpenChange?: (next: boolean) => void
  routed?: { route?: string } | null
}

const AUTO_ID = "__auto__"

export function ModelPicker({
  models,
  selection,
  onSelectionChange,
  disabled = false,
  requireImageSupport,
  open,
  onOpenChange,
  routed,
}: ModelPickerProps) {
  const selectedModel = models.find((model) => model.id === selection?.modelId)
  const selectedEffort =
    selection && selectedModel?.efforts.includes(selection.effort)
      ? selection.effort
      : selectedModel?.default_effort
  const pickerDisabled = disabled || models.length === 0 || !onSelectionChange

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-space-1">
      <ModelSelect
        models={models}
        leadingOption={{
          value: AUTO_ID,
          label: formatAutoModelSelection(
            selection ? undefined : routed?.route
          ),
          icon: <SparkleIcon size={16} />,
        }}
        value={selection?.modelId ?? AUTO_ID}
        placeholder={selection?.modelId ?? "Select a model"}
        requireImageSupport={requireImageSupport}
        open={open}
        onOpenChange={onOpenChange}
        disabled={pickerDisabled}
        side="top"
        onChange={(value) => {
          if (value === AUTO_ID) {
            onSelectionChange?.(null)
            return
          }
          if (value === selectedModel?.id) return
          const next = models.find((model) => model.id === value)
          if (next) {
            onSelectionChange?.({
              modelId: next.id,
              effort: next.default_effort,
            })
          }
        }}
      />
      {selectedModel && selectedEffort && (
        <Select
          aria-label="Reasoning effort"
          variant="plain"
          size="sm"
          side="top"
          value={selectedEffort}
          options={selectedModel.efforts.map((effort) => ({
            value: effort,
            label: formatEffort(effort),
          }))}
          disabled={pickerDisabled}
          onChange={(effort) => {
            if (effort)
              onSelectionChange?.({ modelId: selectedModel.id, effort })
          }}
        />
      )}
    </div>
  )
}
