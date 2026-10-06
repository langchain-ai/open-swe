import { useState } from "react"
import { Link, useNavigate } from "@tanstack/react-router"
import {
  CheckIcon,
  ClockIcon,
  GithubLogoIcon,
  KanbanIcon,
  PencilSimpleIcon,
  PlusIcon,
  SlackLogoIcon,
  TrashIcon,
} from "@phosphor-icons/react"

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
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Switch } from "@langchain/gtm-platform-design-system/ui/switch"
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
const SENDER_ITEMS: Array<{ value: SlackTriggerSenders; label: string }> = [
  { value: "anyone", label: "Anyone" },
  { value: "people", label: "People" },
  { value: "bots", label: "Bots" },
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
      <header className="flex items-center justify-between gap-3 px-6 py-4 max-md:pt-14">
        <div className="flex min-w-0 items-center gap-1.5 text-meta text-ink-subtle/70">
          <Link
            to="/agents/automations"
            className="shrink-0 transition-colors hover:text-ink"
          >
            Automations
          </Link>
          <span className="shrink-0">/</span>
          <span className="truncate text-ink">
            {name.trim() || "New automation"}
          </span>
        </div>
        {canManage && (
          <div className="flex shrink-0 items-center gap-2">
            {mode === "edit" && (
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={handleDelete}
                disabled={deleteSchedule.isPending}
                aria-label="Delete automation"
                className="text-ink-subtle/70 hover:text-risk"
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
          <p className="mb-4 rounded-compact border border-line bg-panel px-3 py-2 text-meta text-ink-subtle">
            This workspace automation is read-only. Ask a workspace admin to
            change it.
          </p>
        )}
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={!canManage}
          placeholder="Untitled automation"
          className="w-full bg-transparent text-title font-medium text-ink outline-none placeholder:text-ink-subtle/70"
        />

        <div className="mt-3 flex items-center gap-3 text-label">
          <div className="flex items-center gap-2">
            <Switch
              checked={enabled}
              onCheckedChange={setEnabled}
              disabled={!canManage}
            />
            <span className="text-ink-subtle">
              {enabled ? "Active" : "Paused"}
            </span>
          </div>
          <span className="text-line">|</span>
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
          <p role="alert" className="mt-2 text-label text-risk">
            Workspace {schedule?.workspace} no longer exists, so runs are
            refused. Pick another workspace and save.
          </p>
        )}

        <SectionLabel>Triggers</SectionLabel>
        <div className="flex flex-col gap-2">
          {drafts.map((draft) => {
            const problem = draftProblem(draft)
            const hint = (fallback: string) => (
              <p
                className={cn(
                  "mt-2 text-label",
                  problem ? "text-risk" : "text-ink-subtle/70"
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
                  icon={<ClockIcon className="size-4" />}
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
                        <PencilSimpleIcon className="size-3.5" />
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
                      className="w-full bg-transparent font-mono text-body text-ink outline-none placeholder:text-ink-subtle/70"
                    />
                  ) : (
                    <p className="text-body text-ink">
                      {describeCron(draft.cron)}{" "}
                      <span className="font-mono text-meta text-ink-subtle/70">
                        {draft.cron}
                      </span>
                    </p>
                  )}
                  {problem && (
                    <p className="mt-1 text-label text-risk">{problem}</p>
                  )}
                </TriggerCard>
              )
            }
            if (draft.kind === "github") {
              return (
                <TriggerCard
                  key={draft.key}
                  icon={<GithubLogoIcon className="size-4" />}
                  removeLabel="Remove GitHub trigger"
                  onRemove={() => removeDraft(draft.key)}
                  canManage={canManage}
                >
                  <div className="text-body text-ink">
                    <RepoSelector
                      repos={reposQuery.data?.repositories}
                      selectedRepo={draft.repo}
                      onRepoChange={(repo) => set({ repo })}
                      placeholder="Choose repository"
                      triggerClassName="text-ink-subtle"
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
                  icon={<SlackLogoIcon className="size-4" />}
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
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-meta text-ink-subtle">
                    <span>From</span>
                    <div className="flex overflow-hidden rounded-badge border border-line">
                      {SENDER_ITEMS.map((item) => (
                        <button
                          key={item.value}
                          type="button"
                          aria-pressed={draft.senders === item.value}
                          onClick={() => set({ senders: item.value })}
                          disabled={!canManage}
                          className={cn(
                            "px-2 py-1 transition-colors disabled:pointer-events-none",
                            draft.senders === item.value
                              ? "bg-primary/20 text-ink"
                              : "hover:text-ink"
                          )}
                        >
                          {item.label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_9rem]">
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
                icon={<KanbanIcon className="size-4" />}
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
                <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_1fr_9rem]">
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
              className="flex items-center gap-1.5 self-start rounded-compact border border-dashed border-line px-3 py-2 text-meta text-ink-subtle transition-colors hover:border-ink/30 hover:text-ink"
            >
              <PlusIcon className="size-3.5" />
              Add trigger
            </TriggerMenu>
          )}
          {!canManage && drafts.length === 0 && (
            <p className="text-meta text-ink-subtle/70">No triggers.</p>
          )}
        </div>

        <SectionLabel>Agent Instructions</SectionLabel>
        <div className="rounded-control border border-line bg-panel p-3">
          <SlackChannelTextarea
            bare
            value={prompt}
            onValueChange={setPrompt}
            disabled={!canManage}
            placeholder="What should Open SWE do each time this runs?"
            rows={5}
            className="w-full resize-none bg-transparent text-body leading-relaxed text-ink outline-none placeholder:text-ink-subtle/70"
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
            <label className="mt-3 flex cursor-pointer items-start gap-2 border-t border-line/60 pt-3">
              <input
                type="checkbox"
                checked={adminThread}
                onChange={(event) => setAdminThread(event.target.checked)}
                disabled={!canManage}
                className="mt-0.5 size-4 accent-risk"
              />
              <span>
                <span className="block text-label font-medium text-ink">
                  Run as admin thread
                </span>
                <span className="mt-0.5 block text-meta text-ink-subtle/70">
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
      </div>
    </div>
  )
}

const CARD_ACTION =
  "rounded-tick p-1 text-ink-subtle/70 hover:bg-hover hover:text-ink"

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
    <div className="mt-2 flex flex-wrap gap-1.5">
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
              "inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-label transition-colors disabled:pointer-events-none",
              on
                ? "border-primary/60 bg-primary/20 text-ink"
                : "border-line text-ink-subtle hover:border-ink/30 hover:text-ink"
            )}
          >
            {on && <CheckIcon className="size-3 text-primary" />}
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
    <label className="flex flex-col gap-1 text-meta text-ink-subtle">
      {label}
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        inputMode={inputMode}
        disabled={disabled}
        className="rounded-badge border border-line bg-transparent px-2 py-1 text-body text-ink outline-none placeholder:text-ink-subtle/60 focus:border-ink/30"
      />
    </label>
  )
}

function TriggerCard({
  icon,
  children,
  actions,
  removeLabel,
  onRemove,
  canManage,
}: {
  icon: React.ReactNode
  children: React.ReactNode
  actions?: React.ReactNode
  removeLabel: string
  onRemove: () => void
  canManage: boolean
}) {
  return (
    <div className="flex items-start gap-3 rounded-control border border-line bg-panel px-3 py-2.5">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-compact bg-muted text-ink-subtle">
        {icon}
      </div>
      <div className="min-w-0 flex-1 py-0.5">{children}</div>
      {canManage && (
        <div className="flex shrink-0 items-center gap-0.5">
          {actions}
          <button
            type="button"
            onClick={onRemove}
            aria-label={removeLabel}
            className={CARD_ACTION}
          >
            <TrashIcon className="size-3.5" />
          </button>
        </div>
      )}
    </div>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mt-8 mb-2 text-meta font-medium text-ink-subtle">
      {children}
    </h2>
  )
}
