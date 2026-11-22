import { useState, type ReactNode } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { RadioCard } from "@langchain/macaw-components/RadioCard"
import { RadioGroup } from "@langchain/macaw-components/RadioGroup"
import { Text } from "@langchain/macaw-components/Text"
import { Textarea } from "@langchain/macaw-components/Textarea"

import { ConfirmDialog } from "@/components/ConfirmDialog"
import {
  CHANNEL_PROMPT_TEMPLATES,
  type ChannelPromptTemplate,
} from "@/features/settings/lib/channel-prompt-templates"

export function ChannelPromptForm({
  label,
  prompt,
  disabled,
  onSave,
  onCancel,
  mode = "edit",
  canSave = true,
  changed = false,
  promptRequired = mode === "add",
  saveLabel,
  error,
  children,
}: {
  label: string
  prompt: string
  disabled: boolean
  onSave: (prompt: string) => void
  onCancel: () => void
  mode?: "edit" | "add"
  canSave?: boolean
  changed?: boolean
  promptRequired?: boolean
  saveLabel?: string
  error?: ReactNode
  children?: ReactNode
}) {
  const [draft, setDraft] = useState(prompt)
  const [selected, setSelected] = useState<ChannelPromptTemplate | null>(null)
  const [replacement, setReplacement] = useState<ChannelPromptTemplate | null>(
    null
  )
  const applyTemplate = (template: ChannelPromptTemplate) => {
    setDraft(template.prompt)
    setSelected(template)
    setReplacement(null)
  }

  return (
    <div className="flex min-w-0 flex-col gap-space-3">
      <div className="flex flex-col gap-space-2">
        <Text variant="sm" weight="medium">
          Start from a Template
        </Text>
        <RadioGroup
          aria-label="Instruction template"
          value={selected?.id ?? ""}
          disabled={disabled}
          onValueChange={(id) => {
            const template = CHANNEL_PROMPT_TEMPLATES.find(
              (item) => item.id === id
            )
            if (!template) return
            if (draft.trim() && draft !== template.prompt)
              setReplacement(template)
            else applyTemplate(template)
          }}
          className="grid grid-cols-2 gap-space-2 sm:grid-cols-4"
        >
          {CHANNEL_PROMPT_TEMPLATES.map((template) => (
            <RadioCard
              key={template.id}
              value={template.id}
              aria-label={template.name}
              disabled={disabled}
              className="items-center px-space-3 py-space-2"
            >
              <Text as="span" variant="sm" weight="medium">
                {template.name}
              </Text>
            </RadioCard>
          ))}
        </RadioGroup>
        <p className="text-xs text-secondary">
          {selected
            ? `${selected.description} Copied into this channel, not linked.`
            : "Pick a template to replace the instructions below, or edit them directly."}
        </p>
      </div>
      {selected && selected.id !== "custom" && (
        <details className="text-xs text-secondary">
          <summary className="cursor-pointer">Template source</summary>
          <p className="pt-space-1">{selected.source}</p>
        </details>
      )}
      {selected?.id === "alerts" && (
        <p className="text-xs text-secondary">
          Bot alerts still require an allowlisted bot and the existing
          bot-trigger settings. This template does not change them.
        </p>
      )}
      <Textarea
        label="Channel Instructions"
        aria-label={`Instructions for ${label}`}
        size="md"
        rows={10}
        maxLength={32000}
        value={draft}
        onChange={setDraft}
        disabled={disabled}
        placeholder="Describe when Robin should respond, how it should help, and who to involve."
        hintText={
          promptRequired
            ? "Add instructions before listening. They guide behavior; they do not grant access or change bot settings."
            : "These instructions guide behavior; they do not grant access or change bot settings. Leave blank for general assistance."
        }
      />
      {children}
      {error && (
        <p role="alert" className="text-sm text-error-secondary">
          {error}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-space-2">
        <Button
          size="xs"
          color="primary"
          disabled={
            disabled ||
            !canSave ||
            (promptRequired && !draft.trim()) ||
            (mode === "edit" && draft.trim() === prompt && !changed)
          }
          onClick={() => onSave(draft.trim())}
        >
          {saveLabel ??
            (mode === "add" ? "Save and start listening" : "Save instructions")}
        </Button>
        <Button
          size="xs"
          color="secondary"
          variant="outlined"
          disabled={disabled}
          onClick={onCancel}
        >
          Cancel
        </Button>
      </div>
      <ConfirmDialog
        open={replacement !== null}
        onOpenChange={(open) => {
          if (!open) setReplacement(null)
        }}
        title="Replace channel instructions?"
        description={`The ${replacement?.name ?? "selected"} template will replace the instructions in this editor. Nothing changes in Slack until you save.`}
        confirmLabel="Replace instructions"
        onConfirm={() => {
          if (replacement) applyTemplate(replacement)
        }}
      />
    </div>
  )
}
