import { CheckIcon, PlusIcon } from "@langchain/macaw-components/icons"
import { Text } from "@langchain/macaw-components/Text"
import { useState } from "react"
import { Link, useNavigate } from "@tanstack/react-router"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Checkbox } from "@langchain/macaw-components/Checkbox"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Input } from "@langchain/macaw-components/Input"
import { Switch } from "@langchain/macaw-components/Switch"
import { ClockIcon } from "@phosphor-icons/react/dist/ssr/Clock"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { KanbanIcon } from "@phosphor-icons/react/dist/ssr/Kanban"
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/ssr/PencilSimple"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"

import type { ModelOption } from "@/lib/api"
import type {
  AgentSchedule,
  GitHubTriggerEvent,
  LinearTriggerEvent,
  SlackTriggerEvent,
  SlackTriggerSenders,
} from "@/features/agents/lib/types"
import { AUTOMATION_EVENT_PROVIDERS } from "@/features/agents/lib/types"
import type { AutomationTemplate } from "@/features/automations/lib/automation-templates"
import type { ModelSelection } from "@/features/agents/lib/provider/useModelOptions"
import { RepoSelector } from "@/features/settings/components/RepoSelector"
import { WorkspaceSelector } from "@/features/agents/components/composer/WorkspaceSelector"
import { AutomationRuns } from "@/features/automations/components/AutomationRuns"
import { TriggerMenu } from "@/features/automations/components/TriggerMenu"
import { SlackChannelCombobox } from "@/components/SlackChannelCombobox"
import { SlackChannelTextarea } from "@/components/SlackChannelTextarea"
import { describeCron } from "@/features/automations/lib/cron"
import type { TriggerDraft } from "@/features/automations/lib/triggers"
import {
  draftProblem,
  draftsFor,
  githubDraft,
  linearDraft,
  scheduleDraft,
  slackDraft,
  toTriggers,
} from "@/features/automations/lib/triggers"
import {
  useCreateAgentSchedule,
  useDeleteAgentSchedule,
  useUpdateAgentSchedule,
  useWorkspaceOptions,
} from "@/features/agents/lib/queries"
import { useModelOptions } from "@/features/agents/lib/provider/useModelOptions"
import { ModelPicker } from "@/features/agents/components/ModelPicker"
import { useUnsavedChangesWarning } from "@/features/automations/lib/useUnsavedChangesWarning"
import { useRepos } from "@/lib/profile"
import { DEFAULT_WORKSPACE_SLUG } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

function eventItems<E extends string>(
  events: Record<E, string>
): Array<{ value: E; label: string }> {
  return (Object.entries(events) as Array<[E, string]>).map(
    ([value, label]) => ({ value, label })
  )
}

const GITHUB_EVENT_ITEMS = eventItems<GitHubTriggerEvent>(
  AUTOMATION_EVENT_PROVIDERS.github.events
)
const SLACK_EVENT_ITEMS = eventItems<SlackTriggerEvent>(
  AUTOMATION_EVENT_PROVIDERS.slack.events
)
const LINEAR_EVENT_ITEMS = eventItems<LinearTriggerEvent>(
  AUTOMATION_EVENT_PROVIDERS.linear.events
)
const SENDER_OPTIONS: Array<{ value: SlackTriggerSenders; display: string }> = [
  { value: "anyone", display: "Anyone" },
  { value: "people", display: "People" },
  { value: "bots", display: "Bots" },
]

/** ``list`` with ``item`` toggled, kept in ``order``. */
function toggled<T>(list: Array<T>, item: T, order: Array<T>): Array<T> {
  return order.filter((value) =>
    value === item ? !list.includes(value) : list.includes(value)
  )
}

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
  const session = useSession()
  const canManage = session.data?.is_admin === true
  const reposQuery = useRepos()
  const { models, defaultSelection } = useModelOptions()
  const workspaceOptionsQuery = useWorkspaceOptions(Boolean(session.data))

  const createSchedule = useCreateAgentSchedule()
  const updateSchedule = useUpdateAgentSchedule()
  const deleteSchedule = useDeleteAgentSchedule()

  const [initialDrafts] = useState(() =>
    draftsFor(schedule, template?.schedule)
  )
  const [drafts, setDrafts] = useState<Array<TriggerDraft>>(initialDrafts)
  const [name, setName] = useState(schedule?.name ?? template?.name ?? "")
  const [prompt, setPrompt] = useState(
    schedule?.prompt ?? template?.prompt ?? ""
  )
  const [enabled, setEnabled] = useState(schedule?.enabled ?? true)
  const [adminThread, setAdminThread] = useState(schedule?.adminThread ?? false)
  // null = untouched: an existing automation keeps its workspace, and a new
  // one starts in the instance's default workspace.
  const [workspaceOverride, setWorkspaceOverride] = useState<string | null>(
    null
  )
  const workspaces = workspaceOptionsQuery.data?.workspaces ?? []
  const workspace =
    workspaceOverride ??
    schedule?.workspace ??
    (mode === "create"
      ? (workspaceOptionsQuery.data?.default_slug ?? DEFAULT_WORKSPACE_SLUG)
      : null)
  // A workspace deleted after the automation was saved: its runs are refused
  // until someone picks another, so the picker must stay reachable.
  const savedWorkspaceMissing =
    workspaceOverride === null &&
    !!schedule?.workspace &&
    workspaceOptionsQuery.data !== undefined &&
    !workspaces.some((option) => option.slug === schedule.workspace)
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
      JSON.stringify(toTriggers(drafts)) !==
        JSON.stringify(toTriggers(initialDrafts)) ||
      drafts.length !== initialDrafts.length ||
      enabled !== (schedule?.enabled ?? true) ||
      adminThread !== (schedule?.adminThread ?? false) ||
      (workspaceOverride !== null &&
        workspaceOverride !== (schedule?.workspace ?? null)) ||
      activeSelection?.modelId !== initialSelection?.modelId ||
      activeSelection?.effort !== initialSelection?.effort)
  const allowNavigation = useUnsavedChangesWarning(isDirty)

  const isSaving = createSchedule.isPending || updateSchedule.isPending

  const canSave =
    name.trim().length > 0 &&
    prompt.trim().length > 0 &&
    workspace !== null &&
    !savedWorkspaceMissing &&
    drafts.length > 0 &&
    drafts.every((draft) => draftProblem(draft) === null)

  const updateDraft = (
    key: string,
    change: (draft: TriggerDraft) => TriggerDraft
  ) =>
    setDrafts((current) =>
      current.map((draft) => (draft.key === key ? change(draft) : draft))
    )
  const removeDraft = (key: string) =>
    setDrafts((current) => current.filter((draft) => draft.key !== key))
  // A preset sets the cron; Custom (null) keeps it and switches to text editing.
  const pickSchedule = (key: string | null, cron: string | null) => {
    if (key === null) {
      const draft = scheduleDraft(cron ?? "0 9 * * *")
      setDrafts((current) => [
        ...current,
        cron === null ? { ...draft, custom: true } : draft,
      ])
      return
    }
    updateDraft(key, (draft) =>
      draft.kind === "schedule"
        ? cron === null
          ? { ...draft, custom: true }
          : { ...draft, cron, custom: false }
        : draft
    )
  }
  const toggleEvent = (key: string, event: string) =>
    updateDraft(key, (draft) => {
      switch (draft.kind) {
        case "github":
          return {
            ...draft,
            events: toggled(
              draft.events,
              event as GitHubTriggerEvent,
              GITHUB_EVENT_ITEMS.map((item) => item.value)
            ),
          }
        case "slack":
          return {
            ...draft,
            events: toggled(
              draft.events,
              event as SlackTriggerEvent,
              SLACK_EVENT_ITEMS.map((item) => item.value)
            ),
          }
        case "linear":
          return {
            ...draft,
            events: toggled(
              draft.events,
              event as LinearTriggerEvent,
              LINEAR_EVENT_ITEMS.map((item) => item.value)
            ),
          }
        default:
          return draft
      }
    })

  const handleSave = () => {
    if (!canSave || workspace === null) return
    const modelIsReal = models.some((m) => m.id === activeSelection?.modelId)
    const modelId = modelIsReal ? (activeSelection?.modelId ?? null) : null
    const effort = modelIsReal ? (activeSelection?.effort ?? null) : null

    if (mode === "create") {
      createSchedule.mutate(
        {
          name: name.trim(),
          prompt: prompt.trim(),
          triggers: toTriggers(drafts),
          admin_thread: adminThread,
          model_id: modelId,
          effort,
          workspace,
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
          triggers: toTriggers(drafts),
          admin_thread: adminThread,
          model_id: modelId,
          effort,
          enabled,
          ...(workspaceOverride !== null
            ? { workspace: workspaceOverride }
            : {}),
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

  const handleDelete = () => {
    if (!schedule) return
    if (!window.confirm(`Delete "${schedule.name}"?`)) return
    deleteSchedule.mutate(schedule.id, {
      onSuccess: () => {
        allowNavigation()
        navigate({ to: "/agents/automations" })
      },
    })
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <header className="flex items-center justify-between gap-space-3 px-space-5 py-space-4 max-md:pt-space-8">
        <div className="flex min-w-0 items-center gap-space-2 text-xs text-tertiary">
          <Link
            to="/agents/automations"
            className="shrink-0 transition-colors hover:text-primary"
          >
            Automations
          </Link>
          <span className="shrink-0">/</span>
          <span className="truncate text-primary">
            {name.trim() || "New automation"}
          </span>
        </div>
        {canManage && (
          <div className="flex shrink-0 items-center gap-space-2">
            {mode === "edit" && (
              <IconButton
                icon={TrashIcon}
                label="Delete automation"
                color="error"
                variant="plain"
                size="md"
                onClick={handleDelete}
                disabled={deleteSchedule.isPending}
              />
            )}
            <Button
              size="md"
              onClick={handleSave}
              disabled={!canSave || isSaving}
            >
              {isSaving
                ? "Saving…"
                : mode === "create"
                  ? "Create"
                  : "Save changes"}
            </Button>
          </div>
        )}
      </header>

      <div className="mx-auto w-full max-w-3xl px-space-5 pt-space-2 pb-space-9">
        {!canManage && (
          <Banner intent="neutral" className="mb-space-4">
            This workspace automation is read-only. Ask a workspace admin to
            change it.
          </Banner>
        )}
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={!canManage}
          placeholder="Untitled automation"
          aria-label="Automation name"
          className="w-full bg-transparent text-base font-medium text-primary outline-none placeholder:text-placeholder"
        />

        <div className="mt-space-3 flex items-center gap-space-3 text-xs">
          <Switch
            checked={enabled}
            onChange={setEnabled}
            disabled={!canManage}
            label={enabled ? "Active" : "Paused"}
            labelClassName="text-xs text-secondary"
          />
          <span aria-hidden="true" className="text-quaternary">
            |
          </span>
          <WorkspaceSelector
            workspaces={workspaces}
            selectedSlug={workspace}
            onChange={(slug) => setWorkspaceOverride(slug)}
            disabled={!canManage}
            placeholder="Choose workspace"
            showWithOneWorkspace
          />
        </div>
        {savedWorkspaceMissing && (
          <p role="alert" className="mt-space-2 text-xs text-error-secondary">
            Workspace {schedule?.workspace} no longer exists, so runs are
            refused. Pick another workspace and save.
          </p>
        )}

        <SectionLabel>Triggers</SectionLabel>
        <div className="flex flex-col gap-space-2">
          {drafts.map((draft) => {
            const problem = draftProblem(draft)
            const hint = (fallback: string) => (
              <p
                className={cn(
                  "mt-space-2 text-xs",
                  problem ? "text-error-secondary" : "text-tertiary"
                )}
              >
                {problem ?? fallback}
              </p>
            )
            const set = (change: Partial<TriggerDraft>) =>
              updateDraft(
                draft.key,
                (current) => ({ ...current, ...change }) as TriggerDraft
              )
            if (draft.kind === "schedule") {
              return (
                <TriggerCard
                  key={draft.key}
                  icon={ClockIcon}
                  removeLabel="Remove schedule"
                  onRemove={() => removeDraft(draft.key)}
                  canManage={canManage}
                  actions={
                    canManage && (
                      <TriggerMenu
                        onSchedule={(cron) => pickSchedule(draft.key, cron)}
                        aria-label="Change schedule"
                        className={CARD_ACTION}
                      >
                        <PencilSimpleIcon size={14} weight="regular" />
                      </TriggerMenu>
                    )
                  }
                >
                  {draft.custom ? (
                    <input
                      value={draft.cron}
                      onChange={(e) => set({ cron: e.target.value })}
                      disabled={!canManage}
                      placeholder="0 9 * * 1-5"
                      aria-label="Cron schedule"
                      className="w-full bg-transparent font-mono text-sm text-primary outline-none placeholder:text-placeholder"
                    />
                  ) : (
                    <p className="text-sm text-primary">
                      {describeCron(draft.cron)}{" "}
                      <span className="font-mono text-xs text-tertiary">
                        {draft.cron}
                      </span>
                    </p>
                  )}
                  {problem && (
                    <p className="mt-space-1 text-xs text-error-secondary">
                      {problem}
                    </p>
                  )}
                </TriggerCard>
              )
            }
            if (draft.kind === "github") {
              return (
                <TriggerCard
                  key={draft.key}
                  icon={GithubLogoIcon}
                  removeLabel="Remove GitHub trigger"
                  onRemove={() => removeDraft(draft.key)}
                  canManage={canManage}
                >
                  <div className="text-sm text-primary">
                    <RepoSelector
                      repos={reposQuery.data?.repositories}
                      selectedRepo={draft.repo}
                      onRepoChange={(repo) => set({ repo })}
                      placeholder="Choose repository"
                      triggerClassName="text-secondary"
                      disabled={!canManage}
                    />
                  </div>
                  <EventChips
                    items={GITHUB_EVENT_ITEMS}
                    selected={draft.events}
                    onToggle={(event) => toggleEvent(draft.key, event)}
                    disabled={!canManage}
                  />
                  {hint(
                    draft.events.includes("pull_request.closed")
                      ? "PR closed also fires when a pull request is merged."
                      : "Event details reach the run as untrusted context."
                  )}
                </TriggerCard>
              )
            }
            if (draft.kind === "slack") {
              return (
                <TriggerCard
                  key={draft.key}
                  icon={SlackLogoIcon}
                  removeLabel="Remove Slack trigger"
                  onRemove={() => removeDraft(draft.key)}
                  canManage={canManage}
                >
                  <SlackChannelCombobox
                    value={draft.channel}
                    onValueChange={(channel) => set({ channel })}
                    disabled={!canManage}
                    placeholder="Choose a channel to watch"
                    aria-label="Slack channel to watch"
                    className="w-full"
                  />
                  <EventChips
                    items={SLACK_EVENT_ITEMS}
                    selected={draft.events}
                    onToggle={(event) => toggleEvent(draft.key, event)}
                    disabled={!canManage}
                  />
                  <div className="mt-space-2 flex flex-wrap items-center gap-space-2 text-xs text-secondary">
                    <span>From</span>
                    <GroupedTabs
                      size="xs"
                      value={draft.senders}
                      onChange={(senders) => set({ senders })}
                      options={SENDER_OPTIONS}
                      disabled={!canManage}
                    />
                  </div>
                  <div className="mt-space-2 grid gap-space-2 sm:grid-cols-[1fr_9rem]">
                    <FilterInput
                      label="Text matches"
                      value={draft.match}
                      onChange={(match) => set({ match })}
                      placeholder="Regular expression, e.g. FIRING|SEV-?1"
                      disabled={!canManage}
                    />
                    <FilterInput
                      label="Max runs per hour"
                      value={draft.maxRunsPerHour}
                      onChange={(maxRunsPerHour) => set({ maxRunsPerHour })}
                      placeholder="No limit"
                      inputMode="numeric"
                      disabled={!canManage}
                    />
                  </div>
                  {hint(
                    "Top-level messages only. Open SWE must be in the channel; messages reach the run as untrusted context."
                  )}
                </TriggerCard>
              )
            }
            return (
              <TriggerCard
                key={draft.key}
                icon={KanbanIcon}
                removeLabel="Remove Linear trigger"
                onRemove={() => removeDraft(draft.key)}
                canManage={canManage}
              >
                <FilterInput
                  label="Team key"
                  value={draft.team}
                  onChange={(team) => set({ team: team.toUpperCase() })}
                  placeholder="ENG"
                  disabled={!canManage}
                />
                <EventChips
                  items={LINEAR_EVENT_ITEMS}
                  selected={draft.events}
                  onToggle={(event) => toggleEvent(draft.key, event)}
                  disabled={!canManage}
                />
                <div className="mt-space-2 grid gap-space-2 sm:grid-cols-[1fr_1fr_9rem]">
                  <FilterInput
                    label="Labels"
                    value={draft.labels}
                    onChange={(labels) => set({ labels })}
                    placeholder="Any; comma-separated"
                    disabled={!canManage}
                  />
                  <FilterInput
                    label="Project"
                    value={draft.project}
                    onChange={(project) => set({ project })}
                    placeholder="Any"
                    disabled={!canManage}
                  />
                  <FilterInput
                    label="Max runs per hour"
                    value={draft.maxRunsPerHour}
                    onChange={(maxRunsPerHour) => set({ maxRunsPerHour })}
                    placeholder="No limit"
                    inputMode="numeric"
                    disabled={!canManage}
                  />
                </div>
                {hint(
                  draft.events.includes("issue.labeled")
                    ? "Label added fires when the issue gains one of these labels, or any label when none are listed."
                    : "Issue details reach the run as untrusted context."
                )}
              </TriggerCard>
            )
          })}
          {canManage && (
            <TriggerMenu
              onSchedule={(cron) => pickSchedule(null, cron)}
              onGitHub={() =>
                setDrafts((current) => [...current, githubDraft()])
              }
              onSlack={() => setDrafts((current) => [...current, slackDraft()])}
              onLinear={() =>
                setDrafts((current) => [...current, linearDraft()])
              }
              className="flex items-center gap-space-2 self-start rounded-lg border border-dashed border-default px-space-3 py-space-2 text-xs text-secondary transition-colors hover:bg-surface-level-1-hover hover:text-primary"
            >
              <PlusIcon size={14} weight="regular" />
              Add trigger
            </TriggerMenu>
          )}
          {!canManage && drafts.length === 0 && (
            <p className="text-xs text-tertiary">No triggers.</p>
          )}
        </div>

        <SectionLabel>Agent Instructions</SectionLabel>
        <div className="rounded-xl border border-default bg-surface-level-1 p-space-3">
          <SlackChannelTextarea
            bare
            value={prompt}
            onValueChange={setPrompt}
            disabled={!canManage}
            placeholder="What should Open SWE do each time this runs?"
            rows={5}
            className="w-full resize-none bg-transparent text-sm leading-relaxed text-primary outline-none placeholder:text-placeholder"
          />
          <div className="mt-space-2 flex items-center">
            <ModelPicker
              models={models}
              selection={activeSelection}
              onSelectionChange={setSelectionOverride}
              disabled={!canManage}
            />
          </div>
          {session.data?.is_admin === true && (
            <div className="mt-space-3 border-t border-subtle pt-space-3">
              <Checkbox
                checked={adminThread}
                onCheckedChange={(checked) => setAdminThread(checked === true)}
                disabled={!canManage}
                label="Run as admin thread"
                labelClassName="text-xs font-medium text-primary"
                aria-describedby="automation-admin-thread-description"
              />
              <p
                id="automation-admin-thread-description"
                className="mt-0.5 pl-space-5 text-xs text-tertiary"
              >
                Allow this automation to use workspace admin capabilities.
              </p>
            </div>
          )}
        </div>

        {mode === "edit" && schedule && (
          <>
            <SectionLabel>Recent runs</SectionLabel>
            <AutomationRuns automationId={schedule.id} limit={10} />
          </>
        )}
      </div>
    </div>
  )
}

const CARD_ACTION =
  "rounded-sm p-space-1 text-tertiary hover:bg-surface-level-1-hover hover:text-primary"

function EventChips<E extends string>({
  items,
  selected,
  onToggle,
  disabled,
}: {
  items: Array<{ value: E; label: string }>
  selected: Array<E>
  onToggle: (event: E) => void
  disabled: boolean
}) {
  return (
    <div className="mt-space-2 flex flex-wrap gap-space-2">
      {items.map((item) => {
        const on = selected.includes(item.value)
        return (
          <button
            key={item.value}
            type="button"
            aria-pressed={on}
            onClick={() => onToggle(item.value)}
            disabled={disabled}
            className={cn(
              "inline-flex items-center gap-space-1 rounded-full border px-space-2 py-space-1 text-xs transition-colors disabled:pointer-events-none",
              on
                ? "border-brand bg-brand-subtle text-primary"
                : "border-default text-secondary hover:bg-surface-level-1-hover hover:text-primary"
            )}
          >
            {on && (
              <CheckIcon size={12} weight="bold" className="text-icon-brand" />
            )}
            {item.label}
          </button>
        )
      })}
    </div>
  )
}

function FilterInput({
  label,
  value,
  onChange,
  placeholder,
  disabled,
  inputMode,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder: string
  disabled: boolean
  inputMode?: "numeric"
}) {
  return (
    <Input
      size="sm"
      label={label}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      inputMode={inputMode}
      disabled={disabled}
    />
  )
}

function TriggerCard({
  icon: Icon,
  children,
  actions,
  removeLabel,
  onRemove,
  canManage,
}: {
  icon: IconComponent
  children: React.ReactNode
  actions?: React.ReactNode
  removeLabel: string
  onRemove: () => void
  canManage: boolean
}) {
  return (
    <div className="flex items-start gap-space-3 rounded-xl border border-default bg-surface-level-1 px-space-3 py-space-2">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-surface-level-2 text-secondary">
        <Icon size={16} weight="regular" />
      </div>
      <div className="min-w-0 flex-1 py-0.5">{children}</div>
      {canManage && (
        <div className="flex shrink-0 items-center gap-0.5">
          {actions}
          <IconButton
            icon={TrashIcon}
            label={removeLabel}
            color="secondary"
            variant="plain"
            onClick={onRemove}
          />
        </div>
      )}
    </div>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <Text
      as="h2"
      variant="sm"
      weight="medium"
      color="secondary"
      className="mt-space-6 mb-space-2"
    >
      {children}
    </Text>
  )
}
