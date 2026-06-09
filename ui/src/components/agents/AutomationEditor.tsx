import { useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate } from "@tanstack/react-router"
import {
  CaretDownIcon,
  CheckIcon,
  ClockIcon,
  TrashIcon,
} from "@phosphor-icons/react"

import type { ModelOption } from "@/lib/api"
import type { AgentSchedule } from "@/lib/agents/types"
import type { ModelSelection } from "@/lib/agents/provider/useModelOptions"
import { RepoSelector } from "@/components/agents/RepoSelector"
import { ScheduleTriggerPicker } from "@/components/agents/ScheduleTriggerPicker"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { describeCron, presetForCron } from "@/lib/agents/cron"
import {
  useCreateAgentSchedule,
  useDeleteAgentSchedule,
  useUpdateAgentSchedule,
} from "@/lib/agents/queries"
import {
  formatModelSelection,
  useModelOptions,
} from "@/lib/agents/provider/useModelOptions"
import { useRepos } from "@/lib/profile"
import { cn } from "@/lib/utils"

interface AutomationEditorProps {
  mode: "create" | "edit"
  schedule?: AgentSchedule
}

function scheduleToSelection(
  models: Array<ModelOption>,
  schedule?: AgentSchedule
): ModelSelection | null {
  if (!schedule?.model || !schedule.effort) return null
  const supported = models.some(
    (model) =>
      model.id === schedule.model && model.efforts.includes(schedule.effort!)
  )
  return supported
    ? { modelId: schedule.model, effort: schedule.effort }
    : null
}

export function AutomationEditor({ mode, schedule }: AutomationEditorProps) {
  const navigate = useNavigate()
  const reposQuery = useRepos()
  const { models, defaultSelection } = useModelOptions()

  const createSchedule = useCreateAgentSchedule()
  const updateSchedule = useUpdateAgentSchedule()
  const deleteSchedule = useDeleteAgentSchedule()

  const [name, setName] = useState(schedule?.name ?? "")
  const [prompt, setPrompt] = useState(schedule?.prompt ?? "")
  const [cron, setCron] = useState<string | null>(schedule?.schedule ?? null)
  const [customMode, setCustomMode] = useState(
    schedule ? presetForCron(schedule.schedule) === "custom" : false
  )
  const [repo, setRepo] = useState<string | null>(schedule?.repo ?? null)
  const [enabled, setEnabled] = useState(schedule?.enabled ?? true)
  // undefined = untouched (derive from the schedule / default as models load).
  const [selectionOverride, setSelectionOverride] = useState<
    ModelSelection | null | undefined
  >(undefined)

  const activeSelection =
    selectionOverride !== undefined
      ? selectionOverride
      : (scheduleToSelection(models, schedule) ?? defaultSelection)

  const error =
    createSchedule.error || updateSchedule.error || deleteSchedule.error
  const errorMessage = error instanceof Error ? error.message : null
  const isSaving = createSchedule.isPending || updateSchedule.isPending

  const canSave = name.trim().length > 0 && prompt.trim().length > 0 && !!cron

  const onPickTrigger = (value: string | null) => {
    if (value === null) {
      setCustomMode(true)
      setCron((current) => current ?? "0 9 * * *")
    } else {
      setCustomMode(false)
      setCron(value)
    }
  }

  const handleSave = () => {
    if (!canSave || !cron) return
    const modelIsReal = models.some((m) => m.id === activeSelection?.modelId)
    const modelId = modelIsReal ? (activeSelection?.modelId ?? null) : null
    const effort = modelIsReal ? (activeSelection?.effort ?? null) : null

    if (mode === "create") {
      createSchedule.mutate(
        {
          name: name.trim(),
          prompt: prompt.trim(),
          schedule: cron.trim(),
          repo,
          model_id: modelId,
          effort,
        },
        { onSuccess: () => navigate({ to: "/agents/automations" }) }
      )
      return
    }
    if (!schedule) return
    updateSchedule.mutate(
      {
        scheduleId: schedule.id,
        body: {
          name: name.trim(),
          prompt: prompt.trim(),
          schedule: cron.trim(),
          repo: repo ?? "",
          model_id: modelId,
          effort,
          enabled,
        },
      },
      { onSuccess: () => navigate({ to: "/agents/automations" }) }
    )
  }

  const handleDelete = () => {
    if (!schedule) return
    if (!window.confirm(`Delete "${schedule.name}"?`)) return
    deleteSchedule.mutate(schedule.id, {
      onSuccess: () => navigate({ to: "/agents/automations" }),
    })
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <header className="flex items-center justify-between gap-3 px-6 py-4 max-md:pt-14">
        <div className="flex min-w-0 items-center gap-1.5 text-sm text-[var(--ui-text-dim)]">
          <Link
            to="/agents/automations"
            className="shrink-0 transition-colors hover:text-[var(--ui-text)]"
          >
            Automations
          </Link>
          <span className="shrink-0">/</span>
          <span className="truncate text-[var(--ui-text)]">
            {name.trim() || "New automation"}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {mode === "edit" && (
            <Button
              variant="ghost"
              size="icon"
              onClick={handleDelete}
              disabled={deleteSchedule.isPending}
              aria-label="Delete automation"
              className="text-[var(--ui-text-dim)] hover:text-[var(--ui-danger)]"
            >
              <TrashIcon className="size-4" />
            </Button>
          )}
          <Button onClick={handleSave} disabled={!canSave || isSaving}>
            {isSaving
              ? "Saving…"
              : mode === "create"
                ? "Create"
                : "Save changes"}
          </Button>
        </div>
      </header>

      <div className="mx-auto w-full max-w-3xl px-6 pt-2 pb-16">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Untitled automation"
          className="w-full bg-transparent text-2xl font-semibold text-[var(--ui-text)] outline-none placeholder:text-[var(--ui-text-dim)]"
        />

        <div className="mt-3 flex items-center gap-3 text-sm">
          <div className="flex items-center gap-2">
            <Switch checked={enabled} onCheckedChange={setEnabled} />
            <span className="text-[var(--ui-text-muted)]">
              {enabled ? "Active" : "Paused"}
            </span>
          </div>
          <span className="text-[var(--ui-border)]">|</span>
          <RepoSelector
            repos={reposQuery.data?.repositories}
            selectedRepo={repo}
            onRepoChange={setRepo}
            placeholder="No repository"
            triggerClassName="text-[var(--ui-text-muted)]"
          />
        </div>

        <SectionLabel>Triggers</SectionLabel>
        <div className="rounded-xl border border-[var(--ui-border)] bg-[var(--ui-surface)] p-1.5">
          {cron && (
            <div className="flex items-center gap-3 rounded-lg px-3 py-2.5">
              <ClockIcon className="size-4 shrink-0 text-[var(--ui-text-muted)]" />
              {customMode ? (
                <input
                  value={cron}
                  onChange={(e) => setCron(e.target.value)}
                  placeholder="0 9 * * 1-5"
                  className="flex-1 bg-transparent font-mono text-sm text-[var(--ui-text)] outline-none placeholder:text-[var(--ui-text-dim)]"
                />
              ) : (
                <span className="flex-1 text-sm text-[var(--ui-text)]">
                  {describeCron(cron)}
                </span>
              )}
              <button
                type="button"
                onClick={() => {
                  setCron(null)
                  setCustomMode(false)
                }}
                aria-label="Remove trigger"
                className="shrink-0 rounded p-1 text-[var(--ui-text-dim)] hover:bg-[var(--ui-panel-2)] hover:text-[var(--ui-text)]"
              >
                <TrashIcon className="size-3.5" />
              </button>
            </div>
          )}
          {cron && <div className="mx-3 h-px bg-[var(--ui-border-subtle)]" />}
          <ScheduleTriggerPicker
            onSelect={onPickTrigger}
            triggerLabel={cron ? "Change trigger" : "Add Trigger"}
          />
        </div>

        <SectionLabel>Agent Instructions</SectionLabel>
        <div className="rounded-xl border border-[var(--ui-border)] bg-[var(--ui-surface)] p-3">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="What should Open SWE do each time this runs?"
            rows={5}
            className="w-full resize-none bg-transparent text-sm leading-relaxed text-[var(--ui-text)] outline-none placeholder:text-[var(--ui-text-dim)]"
          />
          <div className="mt-2 flex items-center">
            <ModelPicker
              models={models}
              selection={activeSelection}
              onSelectionChange={setSelectionOverride}
            />
          </div>
        </div>

        {errorMessage && (
          <p className="mt-4 text-sm text-[var(--ui-danger)]">{errorMessage}</p>
        )}
      </div>
    </div>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mt-8 mb-2 text-sm font-medium text-[var(--ui-text-muted)]">
      {children}
    </h2>
  )
}

function ModelPicker({
  models,
  selection,
  onSelectionChange,
}: {
  models: Array<ModelOption>
  selection: ModelSelection | null
  onSelectionChange: (next: ModelSelection) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  const combos = useMemo<Array<ModelSelection>>(() => {
    const list: Array<ModelSelection> = []
    for (const model of models) {
      for (const effort of model.efforts) {
        list.push({ modelId: model.id, effort })
      }
    }
    return list
  }, [models])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  const disabled = combos.length === 0

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
        className="flex items-center gap-1 text-sm text-[var(--ui-text-muted)] transition-opacity hover:opacity-80 disabled:opacity-60"
      >
        <span>{formatModelSelection(models, selection)}</span>
        {!disabled && <CaretDownIcon className="size-3.5 opacity-60" />}
      </button>
      {open && combos.length > 0 && (
        <div className="absolute bottom-full left-0 z-50 mb-1 max-h-72 overflow-y-auto rounded-lg border border-[var(--ui-border)] bg-[var(--ui-surface)] py-1 shadow-lg">
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
                  onSelectionChange(combo)
                  setOpen(false)
                }}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm whitespace-nowrap transition-colors hover:bg-[var(--ui-panel-2)]",
                  selected
                    ? "text-[var(--ui-text)]"
                    : "text-[var(--ui-text-muted)]"
                )}
              >
                {formatModelSelection(models, combo)}
                {selected && (
                  <CheckIcon className="ml-auto size-3.5 text-[var(--ui-text-dim)]" />
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
