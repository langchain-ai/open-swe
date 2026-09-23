import { useState } from "react"
import { Link, useNavigate } from "@tanstack/react-router"
import { ClockIcon, TrashIcon } from "@phosphor-icons/react"

import type { ModelOption } from "@/lib/api"
import type {
  AgentSchedule,
  AutomationTrigger,
  SlackNotificationMode,
} from "@/features/agents/lib/types"
import type { AutomationTemplate } from "@/features/automations/lib/automation-templates"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import { RepoSelector } from "@/features/settings/components/RepoSelector"
import { AutomationRuns } from "@/features/automations/components/AutomationRuns"
import { ScheduleTriggerPicker } from "@/features/automations/components/ScheduleTriggerPicker"
import { useConfirm } from "@/components/ConfirmDialog"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { TooltipIconButton } from "@/components/ui/tooltip-icon-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  describeCron,
  isDescribableCron,
} from "@/features/automations/lib/cron"
import {
  useCreateAgentSchedule,
  useDeleteAgentSchedule,
  useUpdateAgentSchedule,
} from "@/features/agents/lib/queries"
import { useModelOptions } from "@/features/agents/lib/provider/useModelOptions"
import { ModelPicker } from "@/features/agents/components/ModelPicker"
import { useUnsavedChangesWarning } from "@/features/automations/lib/useUnsavedChangesWarning"
import { useRepos } from "@/lib/profile"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

const BARE_FIELD =
  "h-auto rounded-none border-transparent bg-transparent px-0 py-0 text-sm text-foreground focus-visible:border-transparent focus-visible:ring-0 disabled:opacity-100 md:text-sm dark:bg-transparent"

interface AutomationEditorProps {
  mode: "create" | "edit"
  schedule?: AgentSchedule
  /** Seeds the form in create mode when the user starts from a template. */
  template?: AutomationTemplate
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
  return supported ? { modelId: schedule.model, effort: schedule.effort } : null
}

export function AutomationEditor({
  mode,
  schedule,
  template,
}: AutomationEditorProps) {
  const navigate = useNavigate()
  const confirm = useConfirm()
  const session = useSession()
  const canManage = session.data?.is_admin === true
  const reposQuery = useRepos()
  const { models, defaultSelection } = useModelOptions()

  const createSchedule = useCreateAgentSchedule()
  const updateSchedule = useUpdateAgentSchedule()
  const deleteSchedule = useDeleteAgentSchedule()

  const initialCron = schedule?.schedule ?? template?.schedule ?? null
  const initialTrigger = schedule?.trigger ?? "schedule"
  const [trigger, setTrigger] = useState<AutomationTrigger>(initialTrigger)
  const [name, setName] = useState(schedule?.name ?? template?.name ?? "")
  const [prompt, setPrompt] = useState(
    schedule?.prompt ?? template?.prompt ?? ""
  )
  const [cron, setCron] = useState<string | null>(initialCron)
  const [customMode, setCustomMode] = useState(
    initialCron ? !isDescribableCron(initialCron) : false
  )
  const [repo, setRepo] = useState<string | null>(schedule?.repo ?? null)
  const [slackChannelId, setSlackChannelId] = useState(
    schedule?.slackChannelId ?? ""
  )
  const [slackNotificationMode, setSlackNotificationMode] =
    useState<SlackNotificationMode>(schedule?.slackNotificationMode ?? "always")
  const [enabled, setEnabled] = useState(schedule?.enabled ?? true)
  const [adminThread, setAdminThread] = useState(schedule?.adminThread ?? false)
  // undefined = untouched (derive from the schedule / default as models load).
  const [selectionOverride, setSelectionOverride] = useState<
    ModelSelection | null | undefined
  >(undefined)

  const initialSelection =
    scheduleToSelection(models, schedule) ?? defaultSelection
  const activeSelection =
    selectionOverride !== undefined ? selectionOverride : initialSelection
  const isDirty =
    canManage &&
    (name !== (schedule?.name ?? template?.name ?? "") ||
      prompt !== (schedule?.prompt ?? template?.prompt ?? "") ||
      trigger !== initialTrigger ||
      cron !== initialCron ||
      repo !== (schedule?.repo ?? null) ||
      slackChannelId !== (schedule?.slackChannelId ?? "") ||
      slackNotificationMode !== (schedule?.slackNotificationMode ?? "always") ||
      enabled !== (schedule?.enabled ?? true) ||
      adminThread !== (schedule?.adminThread ?? false) ||
      activeSelection?.modelId !== initialSelection?.modelId ||
      activeSelection?.effort !== initialSelection?.effort)
  const allowNavigation = useUnsavedChangesWarning(isDirty)

  const error =
    createSchedule.error || updateSchedule.error || deleteSchedule.error
  const errorMessage = error instanceof Error ? error.message : null
  const isSaving = createSchedule.isPending || updateSchedule.isPending

  const canSave =
    name.trim().length > 0 &&
    prompt.trim().length > 0 &&
    (trigger === "github_issue_opened" ? !!repo : !!cron)

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
    if (!canSave) return
    const modelIsReal = models.some((m) => m.id === activeSelection?.modelId)
    const modelId = modelIsReal ? (activeSelection?.modelId ?? null) : null
    const effort = modelIsReal ? (activeSelection?.effort ?? null) : null

    if (mode === "create") {
      createSchedule.mutate(
        {
          name: name.trim(),
          prompt: prompt.trim(),
          schedule: trigger === "schedule" ? cron?.trim() : null,
          trigger,
          repo,
          slack_channel_id: slackChannelId.trim() || null,
          slack_notification_mode: slackNotificationMode,
          admin_thread: adminThread,
          model_id: modelId,
          effort,
        },
        {
          onSuccess: () => {
            allowNavigation()
            navigate({ to: "/agents/automations" })
          },
        }
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
          schedule: trigger === "schedule" ? cron?.trim() : null,
          trigger,
          repo: repo ?? "",
          slack_channel_id: slackChannelId.trim() || null,
          slack_notification_mode: slackNotificationMode,
          admin_thread: adminThread,
          model_id: modelId,
          effort,
          enabled,
        },
      },
      {
        onSuccess: () => {
          allowNavigation()
          navigate({ to: "/agents/automations" })
        },
      }
    )
  }

  const handleDelete = async () => {
    if (!schedule) return
    if (
      !(await confirm({
        title: `Delete "${schedule.name}"?`,
        confirmLabel: "Delete",
        destructive: true,
      }))
    )
      return
    deleteSchedule.mutate(schedule.id, {
      onSuccess: () => {
        allowNavigation()
        navigate({ to: "/agents/automations" })
      },
    })
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <header className="flex items-center justify-between gap-3 px-6 py-4 max-md:pt-14">
        <div className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground/70">
          <Link
            to="/agents/automations"
            className="shrink-0 transition-colors hover:text-foreground"
          >
            Automations
          </Link>
          <span className="shrink-0">/</span>
          <span className="truncate text-foreground">
            {name.trim() || "New automation"}
          </span>
        </div>
        {canManage && (
          <div className="flex shrink-0 items-center gap-2">
            {mode === "edit" && (
              <Button
                variant="ghost"
                size="icon"
                onClick={() => void handleDelete()}
                disabled={deleteSchedule.isPending}
                aria-label="Delete automation"
                className="text-muted-foreground/70 hover:text-destructive"
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
        )}
      </header>

      <div className="mx-auto w-full max-w-3xl px-6 pt-2 pb-16">
        {!canManage && (
          <p className="mb-4 rounded-lg border border-border bg-card px-3 py-2 text-xs text-muted-foreground">
            This workspace automation is read-only. Ask a workspace admin to
            change it.
          </p>
        )}
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={!canManage}
          placeholder="Untitled automation"
          aria-label="Automation name"
          className={cn(BARE_FIELD, "text-base font-medium md:text-base")}
        />

        <div className="mt-3 flex items-center gap-3 text-xs">
          <div className="flex items-center gap-2">
            <Switch
              checked={enabled}
              onCheckedChange={setEnabled}
              disabled={!canManage}
            />
            <span className="text-muted-foreground">
              {enabled ? "Active" : "Paused"}
            </span>
          </div>
          <span className="text-border">|</span>
          <RepoSelector
            repos={reposQuery.data?.repositories}
            selectedRepo={repo}
            onRepoChange={setRepo}
            placeholder="No repository"
            triggerClassName="text-muted-foreground"
            disabled={!canManage}
          />
        </div>

        <SectionLabel>Triggers</SectionLabel>
        <div className="rounded-xl border border-border bg-card p-1.5">
          <Select
            value={trigger}
            onValueChange={(value) =>
              value && setTrigger(value as AutomationTrigger)
            }
            disabled={!canManage}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="schedule">Schedule</SelectItem>
              <SelectItem value="github_issue_opened">
                GitHub issue opened
              </SelectItem>
            </SelectContent>
          </Select>
          {trigger === "schedule" && cron && (
            <div className="flex items-center gap-3 rounded-lg px-3 py-2.5">
              <ClockIcon className="size-4 shrink-0 text-muted-foreground" />
              {customMode ? (
                <Input
                  value={cron}
                  onChange={(e) => setCron(e.target.value)}
                  disabled={!canManage}
                  placeholder="0 9 * * 1-5"
                  aria-label="Cron expression"
                  className={cn(BARE_FIELD, "flex-1 font-mono")}
                />
              ) : (
                <span className="flex-1 text-sm text-foreground">
                  {describeCron(cron)}
                </span>
              )}
              <TooltipIconButton
                label="Remove trigger"
                onClick={() => {
                  setCron(null)
                  setCustomMode(false)
                }}
                disabled={!canManage}
              >
                <TrashIcon className="size-3.5" />
              </TooltipIconButton>
            </div>
          )}
          {trigger === "schedule" && cron && (
            <div className="mx-3 h-px bg-border/60" />
          )}
          {canManage && trigger === "schedule" && (
            <ScheduleTriggerPicker
              onSelect={onPickTrigger}
              triggerLabel={cron ? "Change trigger" : "Add Trigger"}
            />
          )}
        </div>

        <SectionLabel>Slack destination</SectionLabel>
        <div className="rounded-xl border border-border bg-card p-3">
          <Input
            value={slackChannelId}
            onChange={(e) => setSlackChannelId(e.target.value)}
            disabled={!canManage}
            placeholder="C0123456789"
            spellCheck={false}
            aria-label="Slack channel ID"
            className={cn(BARE_FIELD, "font-mono")}
          />
          <div className="mt-3 flex items-center justify-between gap-3 border-t border-border/60 pt-3">
            <span className="text-xs text-muted-foreground">
              Notify channel
            </span>
            <Select
              value={slackNotificationMode}
              onValueChange={(value) =>
                value && setSlackNotificationMode(value)
              }
              disabled={!canManage || !slackChannelId.trim()}
            >
              <SelectTrigger className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="always">Every run</SelectItem>
                <SelectItem value="on_action">
                  Only when action is taken
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          <p className="mt-2 text-xs text-muted-foreground/70">
            {slackNotificationMode === "on_action"
              ? "The agent decides whether it performed an action; read-only and no-op runs stay silent."
              : "Each run starts a new thread in the channel."}{" "}
            The Open SWE bot must be a member of the channel.
          </p>
        </div>

        <SectionLabel>Agent Instructions</SectionLabel>
        <div className="rounded-xl border border-border bg-card p-3">
          <Textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={!canManage}
            placeholder="What should Open SWE do each time this runs?"
            rows={5}
            aria-label="Agent instructions"
            className={cn(BARE_FIELD, "field-sizing-fixed leading-relaxed")}
          />
          <div className="mt-2 flex items-center">
            <ModelPicker
              models={models}
              selection={activeSelection}
              onSelectionChange={setSelectionOverride}
              disabled={!canManage}
            />
          </div>
          {session.data?.is_admin === true && (
            <label className="mt-3 flex cursor-pointer items-start gap-2 border-t border-border/60 pt-3">
              <Checkbox
                checked={adminThread}
                onCheckedChange={setAdminThread}
                disabled={!canManage}
                className="mt-0.5 data-checked:border-destructive data-checked:bg-destructive dark:data-checked:bg-destructive"
              />
              <span>
                <span className="block text-xs font-medium text-foreground">
                  Run as admin thread
                </span>
                <span className="mt-0.5 block text-xs text-muted-foreground/70">
                  Allow this automation to use workspace admin capabilities.
                </span>
              </span>
            </label>
          )}
        </div>

        {mode === "edit" && schedule && (
          <>
            <SectionLabel>Recent runs</SectionLabel>
            <AutomationRuns automationId={schedule.id} limit={10} />
          </>
        )}

        {errorMessage && (
          <p className="mt-4 text-xs text-destructive">{errorMessage}</p>
        )}
      </div>
    </div>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mt-8 mb-2 text-xs font-medium text-muted-foreground">
      {children}
    </h2>
  )
}
