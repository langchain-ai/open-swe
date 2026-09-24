import { createFileRoute } from "@tanstack/react-router"
import { useEffect, useRef, useState } from "react"

import type { ModelOption, ProfileUpdate } from "@/lib/api"
import {
  AuthedAppShell,
  SettingsNavRow,
  SettingsRow,
  SettingsSection,
} from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { RepoSelector } from "@/features/settings/components/RepoSelector"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import {
  useOptions,
  usePatchProfile,
  useProfile,
  useRepos,
} from "@/lib/profile"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/cloud-agents")({
  component: CloudAgentsPage,
  head: () => ({ meta: [{ title: pageTitle("Open SWE Agent") }] }),
})

function CloudAgentsPage() {
  const profile = useProfile()
  const options = useOptions()
  const repos = useRepos()
  const save = usePatchProfile()

  const [modelId, setModelId] = useState("")
  const [effortChoice, setEffort] = useState("")
  const [subagentModelId, setSubagentModelId] = useState("")
  const [subagentEffortChoice, setSubagentEffort] = useState("")
  const [defaultRepo, setDefaultRepo] = useState("")
  const [baseBranch, setBaseBranch] = useState("")
  const [branchPrefix, setBranchPrefix] = useState("")
  const initialized = useRef(false)

  const defaultModels = options.data?.models.filter(
    (model) => model.can_be_default !== false
  )
  const firstModel: ModelOption | undefined = defaultModels?.[0]
  const defaultAgentModel =
    options.data?.default_agent_model ?? firstModel?.id ?? ""
  const defaultAgentEffort =
    options.data?.default_agent_reasoning_effort ??
    firstModel?.default_effort ??
    ""
  const defaultSubagentModel =
    options.data?.default_agent_subagent_model ?? defaultAgentModel
  const defaultSubagentEffort =
    options.data?.default_agent_subagent_reasoning_effort ?? defaultAgentEffort
  const currentModel: ModelOption | undefined =
    defaultModels?.find((m) => m.id === modelId) ?? firstModel
  const subagentInheritsMain = subagentModelId === "inherit"
  const currentSubagentModel: ModelOption | undefined = subagentInheritsMain
    ? currentModel
    : (defaultModels?.find((m) => m.id === subagentModelId) ?? firstModel)
  const effort =
    currentModel && !currentModel.efforts.includes(effortChoice)
      ? currentModel.default_effort
      : effortChoice
  const subagentEffort = subagentInheritsMain
    ? effort
    : currentSubagentModel &&
        !currentSubagentModel.efforts.includes(subagentEffortChoice)
      ? currentSubagentModel.default_effort
      : subagentEffortChoice

  useEffect(() => {
    if (!profile.data || initialized.current) return
    const hasModel = !!profile.data.default_model || !!defaultAgentModel
    if (!hasModel) return
    initialized.current = true
    // oxlint-disable-next-line react/set-state-in-effect
    setModelId(profile.data.default_model ?? defaultAgentModel)
    setEffort(profile.data.reasoning_effort ?? defaultAgentEffort)
    setSubagentModelId(profile.data.default_subagent_model ?? "inherit")
    setSubagentEffort(
      profile.data.default_subagent_model == null
        ? (profile.data.reasoning_effort ?? defaultAgentEffort)
        : (profile.data.subagent_reasoning_effort ?? defaultSubagentEffort)
    )
    setDefaultRepo(profile.data.default_repo ?? "")
    setBaseBranch(profile.data.base_branch ?? "")
    setBranchPrefix(profile.data.branch_prefix ?? "")
  }, [
    profile.data,
    defaultAgentModel,
    defaultAgentEffort,
    defaultSubagentModel,
    defaultSubagentEffort,
  ])

  const fallbackModel = defaultAgentModel
  const fallbackEffort = defaultAgentEffort

  const persist = (patch: Partial<ProfileUpdate>) =>
    save.patch(patch, fallbackModel, fallbackEffort)

  const persistDefaults = () => {
    persist({
      default_model: modelId,
      reasoning_effort: effort,
      default_subagent_model: subagentInheritsMain ? null : subagentModelId,
      subagent_reasoning_effort: subagentInheritsMain ? null : subagentEffort,
      default_repo: defaultRepo || null,
      base_branch: baseBranch || null,
      branch_prefix: branchPrefix || null,
    })
  }

  return (
    <AuthedAppShell
      title="Open SWE Agent"
      description="Personal defaults for Open SWE Agent runs you trigger. These settings only apply to your account."
    >
      {() => (
        <>
          <SettingsSection title="Defaults">
            <div className="divide-y divide-border">
              <SettingsRow
                label="Adaptive model routing"
                description="Automatically choose a model for each turn. Inherit uses the org-wide default; Enabled or Disabled overrides it."
                control={
                  <Select
                    value={
                      profile.data?.model_routing_enabled === true
                        ? "enabled"
                        : profile.data?.model_routing_enabled === false
                          ? "disabled"
                          : "inherit"
                    }
                    onValueChange={(v) =>
                      persist({
                        model_routing_enabled:
                          v === "enabled"
                            ? true
                            : v === "disabled"
                              ? false
                              : null,
                      })
                    }
                    disabled={profile.isLoading || save.isPending}
                  >
                    <SelectTrigger className="w-40">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="inherit">
                        Inherit org default
                      </SelectItem>
                      <SelectItem value="enabled">Enabled</SelectItem>
                      <SelectItem value="disabled">Disabled</SelectItem>
                    </SelectContent>
                  </Select>
                }
              />
              <SettingsRow
                label="Recent working contexts"
                description="Include a filtered digest of your recent threads in agent runs."
                control={
                  <Switch
                    checked={
                      profile.data?.recent_thread_context_enabled ?? false
                    }
                    onCheckedChange={(v) =>
                      persist({ recent_thread_context_enabled: v })
                    }
                    disabled={profile.isLoading || save.isPending}
                  />
                }
              />
              <SettingsRow
                label="Default Model"
                description="Used when adaptive routing is off or no model is specified"
                control={
                  <Select
                    value={modelId}
                    onValueChange={(v) => v && setModelId(v)}
                  >
                    <SelectTrigger className="w-40">
                      <SelectValue placeholder="Pick a model" />
                    </SelectTrigger>
                    <SelectContent>
                      {defaultModels?.map((m) => (
                        <SelectItem key={m.id} value={m.id}>
                          {m.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                }
              />
              <SettingsRow
                label="Reasoning Effort"
                description="How hard the model thinks before answering"
                control={
                  <Select
                    value={effort}
                    onValueChange={(v) => v && setEffort(v)}
                  >
                    <SelectTrigger className="w-32">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {currentModel?.efforts.map((e) => (
                        <SelectItem key={e} value={e}>
                          {e}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                }
              />
              <SettingsRow
                label="Default Subagent Model"
                description="Used for delegated tasks; inherit follows your default model and effort"
                control={
                  <Select
                    value={subagentModelId}
                    onValueChange={(v) => v && setSubagentModelId(v)}
                  >
                    <SelectTrigger className="w-40">
                      <SelectValue placeholder="Pick a model" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="inherit">Inherit from main</SelectItem>
                      {defaultModels?.map((m) => (
                        <SelectItem key={m.id} value={m.id}>
                          {m.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                }
              />
              <SettingsRow
                label="Subagent Reasoning Effort"
                description="How hard delegated subagents think before answering"
                control={
                  <Select
                    value={subagentEffort}
                    onValueChange={(v) => v && setSubagentEffort(v)}
                    disabled={subagentInheritsMain}
                  >
                    <SelectTrigger className="w-32">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {currentSubagentModel?.efforts.map((e) => (
                        <SelectItem key={e} value={e}>
                          {e}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                }
              />
              <SettingsRow
                label="Default Repository"
                description="Used when no repository is specified"
                control={
                  repos.data?.repositories.length ? (
                    <div className="w-56">
                      <RepoSelector
                        repos={repos.data.repositories}
                        selectedRepo={defaultRepo || null}
                        onRepoChange={(repo) => setDefaultRepo(repo ?? "")}
                        placeholder="Pick a repository…"
                        emptySelectionLabel="No default repository"
                        triggerClassName="h-7 w-full max-w-none rounded-md border border-input bg-input/20 px-2 py-1.5 text-xs/relaxed text-foreground transition-colors hover:opacity-100 dark:bg-input/30"
                        dropdownClassName="w-56"
                      />
                    </div>
                  ) : (
                    <Input
                      className="w-56"
                      placeholder="owner/repo"
                      value={defaultRepo}
                      onChange={(e) => setDefaultRepo(e.target.value)}
                    />
                  )
                }
              />
              <SettingsRow
                label="Base Branch"
                description="When empty, Cloud Agent will use a repository's default branch (recommended)"
                htmlFor="base-branch"
                control={
                  <Input
                    id="base-branch"
                    className="w-56"
                    placeholder="Branch name…"
                    value={baseBranch}
                    onChange={(e) => setBaseBranch(e.target.value)}
                  />
                }
              />
              <SettingsRow
                label="Branch Prefix"
                description="Prefix for branch names created by Cloud Agent"
                htmlFor="branch-prefix"
                control={
                  <Input
                    id="branch-prefix"
                    className="w-56"
                    placeholder="open-swe/"
                    value={branchPrefix}
                    onChange={(e) => setBranchPrefix(e.target.value)}
                  />
                }
              />
              <div className="flex justify-end px-4 py-3">
                <Button
                  size="sm"
                  onClick={persistDefaults}
                  disabled={save.isPending}
                >
                  {save.isPending ? "Saving…" : "Save defaults"}
                </Button>
              </div>
            </div>
          </SettingsSection>

          <SettingsSection title="Pull Requests">
            <div className="divide-y divide-border">
              <SettingsRow
                label="Automatically fix CI failures"
                description="Agent will attempt to fix failing CI checks and resolve reviewer comments on PRs it opens."
                control={
                  <Switch
                    checked={profile.data?.auto_fix_ci ?? true}
                    onCheckedChange={(v) => persist({ auto_fix_ci: v })}
                  />
                }
              />
            </div>
          </SettingsSection>

          <SettingsSection title="Slack">
            <div className="divide-y divide-border">
              <SettingsRow
                label="Keep my DM as one conversation"
                description="Your whole DM with Open SWE becomes one private thread it always answers in, instead of a new thread for every message."
                control={
                  <Switch
                    checked={profile.data?.dm_session_enabled ?? false}
                    onCheckedChange={(v) => persist({ dm_session_enabled: v })}
                  />
                }
              />
            </div>
          </SettingsSection>

          <SettingsSection title="Rules">
            <SettingsNavRow
              to="/agents/instructions"
              label="Repository Instructions"
              description="Per-repo custom instructions injected into the agent's system prompt."
            />
          </SettingsSection>
        </>
      )}
    </AuthedAppShell>
  )
}
