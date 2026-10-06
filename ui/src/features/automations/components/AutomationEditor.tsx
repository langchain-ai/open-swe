import { useState } from "react"
import type { ReactNode } from "react"
import { Link, useNavigate } from "@tanstack/react-router"

import { ConfirmableAction } from "@langchain/gtm-platform-design-system/patterns/confirmable-action"
import {
  FormField,
  FormSection,
  FormStack,
} from "@langchain/gtm-platform-design-system/patterns/form-field"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import {
  RecordHeader,
  RecordInlineEdit,
} from "@langchain/gtm-platform-design-system/patterns/record-header"
import { Alert, AlertDescription } from "@langchain/gtm-platform-design-system/ui/alert"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button, buttonVariants } from "@langchain/gtm-platform-design-system/ui/button"
import { Checkbox } from "@langchain/gtm-platform-design-system/ui/checkbox"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { IconWell } from "@langchain/gtm-platform-design-system/ui/icon-well"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { HELP_CLASS, LABEL_CLASS } from "@langchain/gtm-platform-design-system/ui/label"
import { ProviderLogo } from "@langchain/gtm-platform-design-system/ui/provider-logos"
import { ScrollArea } from "@langchain/gtm-platform-design-system/ui/scroll-area"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@langchain/gtm-platform-design-system/ui/toggle-group"

import {
  AlertTriangle,
  ArrowLeft,
  Clock,
  GitHub,
  Lock,
  Pencil,
  Plus,
  Trash2,
} from "@/components/glyphs"
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
  const savedName = schedule?.name ?? template?.name ?? ""
  const [name, setName] = useState(savedName)
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
    (name !== savedName ||
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

  const handleDelete = async () => {
    if (!schedule) return
    await deleteSchedule.mutateAsync(schedule.id)
    allowNavigation()
    void navigate({ to: "/agents/automations" })
  }

  return (
    <ScrollArea overflow="vertical" className="h-full min-h-0 min-w-0 flex-1">
      <Stack
        gap="xl"
        className="mx-auto w-full max-w-reading px-6 py-6 max-md:pt-16"
      >
        <Stack gap="sm">
          <Inline>
            <Link
              to="/agents/automations"
              className={buttonVariants({ variant: "ghost", size: "compact" })}
            >
              <Icon icon={ArrowLeft} size="sm" />
              Automations
            </Link>
          </Inline>
          <RecordHeader
            title={
              canManage ? (
                <RecordInlineEdit
                  kind="TITLE"
                  label="Automation name"
                  placeholder="Untitled automation"
                  value={name}
                  onChange={setName}
                  onCommit={() => {}}
                  onCancel={() => setName(savedName)}
                />
              ) : (
                <Box
                  render={<h1 />}
                  className="truncate text-title font-semibold text-ink"
                >
                  {name.trim() || "Untitled automation"}
                </Box>
              )
            }
            status={
              mode === "edit" ? (
                <Badge tone={enabled ? "positive" : "neutral"} dot>
                  {enabled ? "Active" : "Paused"}
                </Badge>
              ) : undefined
            }
            actions={
              canManage ? (
                <>
                  {mode === "edit" && schedule && (
                    <ConfirmableAction
                      title={`Delete "${schedule.name}"?`}
                      description="It stops running, and this cannot be undone."
                      confirmLabel="Delete automation"
                      confirmIcon={Trash2}
                      onConfirm={handleDelete}
                      trigger={
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          aria-label="Delete automation"
                          disabled={deleteSchedule.isPending}
                        >
                          <Icon icon={Trash2} size="sm" />
                        </Button>
                      }
                    />
                  )}
                  <Button onClick={handleSave} disabled={!canSave || isSaving}>
                    {isSaving
                      ? "Saving…"
                      : mode === "create"
                        ? "Create"
                        : "Save changes"}
                  </Button>
                </>
              ) : undefined
            }
          />
        </Stack>

        {!canManage && (
          <Alert tone="neutral" icon={Lock}>
            <AlertDescription>
              This workspace automation is read-only. Ask a workspace admin to
              change it.
            </AlertDescription>
          </Alert>
        )}

        <FormStack>
          <FormSection title="Details">
            <FormField
              label="Workspace"
              help="Every run starts a sandbox from this workspace."
              error={
                savedWorkspaceMissing
                  ? `Workspace ${schedule?.workspace} no longer exists, so runs are refused. Pick another workspace and save.`
                  : undefined
              }
              control={
                <WorkspaceSelector
                  workspaces={workspaces}
                  selectedSlug={workspace}
                  onChange={(slug) => setWorkspaceOverride(slug)}
                  disabled={!canManage}
                  placeholder="Choose workspace"
                  showWithOneWorkspace
                />
              }
            />
            <CheckboxField
              label="Active"
              description="Paused automations keep their setup but do not run."
              checked={enabled}
              onCheckedChange={setEnabled}
              disabled={!canManage}
            />
          </FormSection>

          <FormSection
            title="Triggers"
            description="Run on a schedule, or when GitHub, Slack, or Linear events arrive."
          >
            {drafts.map((draft) => (
              <TriggerEditor
                key={draft.key}
                draft={draft}
                canManage={canManage}
                repos={reposQuery.data?.repositories}
                onChange={(change) => updateDraft(draft.key, change)}
                onRemove={() => removeDraft(draft.key)}
                onPickSchedule={(cron) => pickSchedule(draft.key, cron)}
              />
            ))}
            {canManage && (
              <Inline>
                <TriggerMenu
                  onSchedule={(cron) => pickSchedule(null, cron)}
                  onGitHub={() =>
                    setDrafts((current) => [...current, githubDraft()])
                  }
                  onSlack={() =>
                    setDrafts((current) => [...current, slackDraft()])
                  }
                  onLinear={() =>
                    setDrafts((current) => [...current, linearDraft()])
                  }
                >
                  <Icon icon={Plus} size="sm" />
                  Add trigger
                </TriggerMenu>
              </Inline>
            )}
            {!canManage && drafts.length === 0 && (
              <Box render={<p />} className={HELP_CLASS}>
                No triggers.
              </Box>
            )}
          </FormSection>

          <FormSection title="Agent instructions">
            <FormField
              label="Instructions"
              control={
                <SlackChannelTextarea
                  value={prompt}
                  onValueChange={setPrompt}
                  disabled={!canManage}
                  placeholder="What should Open SWE do each time this runs?"
                  rows={5}
                  className="resize-none"
                />
              }
            />
            <FormField
              label="Model"
              control={
                <ModelPicker
                  models={models}
                  selection={activeSelection}
                  onSelectionChange={setSelectionOverride}
                  disabled={!canManage}
                />
              }
            />
            {session.data?.is_admin === true && (
              <CheckboxField
                label="Run as admin thread"
                description="Allow this automation to use workspace admin capabilities."
                checked={adminThread}
                onCheckedChange={setAdminThread}
                disabled={!canManage}
              />
            )}
          </FormSection>
        </FormStack>

        {mode === "edit" && schedule && (
          <PageSection title="Recent runs">
            <AutomationRuns automationId={schedule.id} limit={10} />
          </PageSection>
        )}
      </Stack>
    </ScrollArea>
  )
}

function CheckboxField({
  label,
  description,
  checked,
  onCheckedChange,
  disabled,
}: {
  label: string
  description: string
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled: boolean
}) {
  return (
    <Inline
      render={<label />}
      gap="sm"
      align="start"
      className={disabled ? undefined : "cursor-pointer"}
    >
      <Checkbox
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        className="mt-0.5"
      />
      <Stack gap="xs" className="min-w-0">
        <Box render={<span />} className={LABEL_CLASS}>
          {label}
        </Box>
        <Box render={<span />} className={HELP_CLASS}>
          {description}
        </Box>
      </Stack>
    </Inline>
  )
}

function TriggerEditor({
  draft,
  canManage,
  repos,
  onChange,
  onRemove,
  onPickSchedule,
}: {
  draft: TriggerDraft
  canManage: boolean
  repos: Parameters<typeof RepoSelector>[0]["repos"]
  onChange: (change: (draft: TriggerDraft) => TriggerDraft) => void
  onRemove: () => void
  onPickSchedule: (cron: string | null) => void
}) {
  const problem = draftProblem(draft)
  const set = (change: Partial<TriggerDraft>) =>
    onChange((current) => ({ ...current, ...change }) as TriggerDraft)

  if (draft.kind === "schedule") {
    return (
      <TriggerCard
        mark={<Icon icon={Clock} size="sm" />}
        title="Schedule"
        removeLabel="Remove schedule"
        onRemove={onRemove}
        canManage={canManage}
        actions={
          canManage && (
            <TriggerMenu
              onSchedule={onPickSchedule}
              aria-label="Change schedule"
              variant="ghost"
              size="icon-sm"
            >
              <Icon icon={Pencil} size="sm" />
            </TriggerMenu>
          )
        }
      >
        {draft.custom ? (
          <FormField
            label="Cron schedule"
            help="Five fields, in UTC."
            error={problem ?? undefined}
            control={
              <Input
                value={draft.cron}
                onChange={(e) => set({ cron: e.target.value })}
                disabled={!canManage}
                placeholder="0 9 * * 1-5"
                className="font-mono"
              />
            }
          />
        ) : (
          <Inline gap="sm" wrap className="text-body text-ink">
            {describeCron(draft.cron)}
            <Box render={<span />} className="font-mono text-meta text-ink-subtle">
              {draft.cron}
            </Box>
          </Inline>
        )}
        {!draft.custom && problem && <TriggerHint problem={problem} />}
      </TriggerCard>
    )
  }

  if (draft.kind === "github") {
    return (
      <TriggerCard
        mark={<Icon icon={GitHub} size="sm" />}
        title={AUTOMATION_EVENT_PROVIDERS.github.label}
        removeLabel="Remove GitHub trigger"
        onRemove={onRemove}
        canManage={canManage}
      >
        <FormField
          label="Repository"
          control={
            <RepoSelector
              repos={repos}
              selectedRepo={draft.repo}
              onRepoChange={(repo) => set({ repo })}
              placeholder="Choose repository"
              disabled={!canManage}
            />
          }
        />
        <EventToggles
          label="GitHub events"
          items={GITHUB_EVENT_ITEMS}
          selected={draft.events}
          onChange={(events) => set({ events })}
          disabled={!canManage}
        />
        <TriggerHint problem={problem}>
          {draft.events.includes("pull_request.closed")
            ? "PR closed also fires when a pull request is merged."
            : "Event details reach the run as untrusted context."}
        </TriggerHint>
      </TriggerCard>
    )
  }

  if (draft.kind === "slack") {
    return (
      <TriggerCard
        mark={<ProviderLogo provider="slack" className="size-3.5" />}
        title={AUTOMATION_EVENT_PROVIDERS.slack.label}
        removeLabel="Remove Slack trigger"
        onRemove={onRemove}
        canManage={canManage}
      >
        <FormField
          label="Channel"
          control={
            <SlackChannelCombobox
              value={draft.channel}
              onValueChange={(channel) => set({ channel })}
              disabled={!canManage}
              placeholder="Choose a channel to watch"
              aria-label="Slack channel to watch"
              className="w-full"
            />
          }
        />
        <EventToggles
          label="Slack events"
          items={SLACK_EVENT_ITEMS}
          selected={draft.events}
          onChange={(events) => set({ events })}
          disabled={!canManage}
        />
        <FormField
          label="From"
          control={
            <ToggleGroup
              aria-label="Senders"
              value={[draft.senders]}
              onValueChange={(value) => {
                const next = value[0]
                if (next) set({ senders: next })
              }}
              disabled={!canManage}
            >
              {SENDER_ITEMS.map((item) => (
                <ToggleGroupItem key={item.value} value={item.value}>
                  {item.label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          }
        />
        <Box className="grid gap-4 sm:grid-cols-3">
          <Box className="sm:col-span-2">
            <FormField
              label="Text matches"
              control={
                <Input
                  value={draft.match}
                  onChange={(e) => set({ match: e.target.value })}
                  placeholder="Regular expression, e.g. FIRING|SEV-?1"
                  disabled={!canManage}
                />
              }
            />
          </Box>
          <RunsPerHourField
            value={draft.maxRunsPerHour}
            onChange={(maxRunsPerHour) => set({ maxRunsPerHour })}
            disabled={!canManage}
          />
        </Box>
        <TriggerHint problem={problem}>
          Top-level messages only. Open SWE must be in the channel; messages
          reach the run as untrusted context.
        </TriggerHint>
      </TriggerCard>
    )
  }

  return (
    <TriggerCard
      mark={<ProviderLogo provider="linear" className="size-3.5" />}
      title={AUTOMATION_EVENT_PROVIDERS.linear.label}
      removeLabel="Remove Linear trigger"
      onRemove={onRemove}
      canManage={canManage}
    >
      <FormField
        label="Team key"
        control={
          <Input
            value={draft.team}
            onChange={(e) => set({ team: e.target.value.toUpperCase() })}
            placeholder="ENG"
            disabled={!canManage}
          />
        }
      />
      <EventToggles
        label="Linear events"
        items={LINEAR_EVENT_ITEMS}
        selected={draft.events}
        onChange={(events) => set({ events })}
        disabled={!canManage}
      />
      <Box className="grid gap-4 sm:grid-cols-3">
        <FormField
          label="Labels"
          control={
            <Input
              value={draft.labels}
              onChange={(e) => set({ labels: e.target.value })}
              placeholder="Any; comma-separated"
              disabled={!canManage}
            />
          }
        />
        <FormField
          label="Project"
          control={
            <Input
              value={draft.project}
              onChange={(e) => set({ project: e.target.value })}
              placeholder="Any"
              disabled={!canManage}
            />
          }
        />
        <RunsPerHourField
          value={draft.maxRunsPerHour}
          onChange={(maxRunsPerHour) => set({ maxRunsPerHour })}
          disabled={!canManage}
        />
      </Box>
      <TriggerHint problem={problem}>
        {draft.events.includes("issue.labeled")
          ? "Label added fires when the issue gains one of these labels, or any label when none are listed."
          : "Issue details reach the run as untrusted context."}
      </TriggerHint>
    </TriggerCard>
  )
}

function RunsPerHourField({
  value,
  onChange,
  disabled,
}: {
  value: string
  onChange: (value: string) => void
  disabled: boolean
}) {
  return (
    <FormField
      label="Max runs per hour"
      control={
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="No limit"
          inputMode="numeric"
          disabled={disabled}
        />
      }
    />
  )
}

function EventToggles<E extends string>({
  label,
  items,
  selected,
  onChange,
  disabled,
}: {
  label: string
  items: Array<{ value: E; label: string }>
  selected: Array<E>
  onChange: (events: Array<E>) => void
  disabled: boolean
}) {
  return (
    <FormField
      label="Events"
      control={
        <ToggleGroup<E>
          multiple
          aria-label={label}
          value={selected}
          onValueChange={(next) =>
            onChange(
              items
                .map((item) => item.value)
                .filter((value) => next.includes(value))
            )
          }
          disabled={disabled}
          className="flex-wrap"
        >
          {items.map((item) => (
            <ToggleGroupItem<E> key={item.value} value={item.value}>
              {item.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      }
    />
  )
}

function TriggerHint({
  problem,
  children,
}: {
  problem: string | null
  children?: ReactNode
}) {
  if (problem) {
    return (
      <Inline gap="sm" align="start" ink="risk" className="text-label">
        <Icon icon={AlertTriangle} size="sm" className="mt-0.5" />
        <Box render={<span />}>{problem}</Box>
      </Inline>
    )
  }
  if (!children) return null
  return (
    <Box render={<p />} className={HELP_CLASS}>
      {children}
    </Box>
  )
}

function TriggerCard({
  mark,
  title,
  children,
  actions,
  removeLabel,
  onRemove,
  canManage,
}: {
  mark: ReactNode
  title: string
  children: ReactNode
  actions?: ReactNode
  removeLabel: string
  onRemove: () => void
  canManage: boolean
}) {
  return (
    <Stack gap="md" bg="panel" border="line" radius="panel" padding="lg">
      <Inline gap="sm" justify="between" className="min-h-control-sm">
        <Inline gap="sm" className="min-w-0">
          <IconWell>{mark}</IconWell>
          <Box render={<span />} className={LABEL_CLASS}>
            {title}
          </Box>
        </Inline>
        {canManage && (
          <Inline gap="xs" className="shrink-0">
            {actions}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={onRemove}
              aria-label={removeLabel}
            >
              <Icon icon={Trash2} size="sm" />
            </Button>
          </Inline>
        )}
      </Inline>
      {children}
    </Stack>
  )
}
