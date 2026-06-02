import { Navigate, createFileRoute } from "@tanstack/react-router"
import { useEffect, useRef, useState } from "react"

import type { ModelOption } from "@/lib/api"
import { AppShell, SettingsRow, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import {
  buildProfileUpdate,
  useOptions,
  useProfile,
  useRepos,
  useSaveProfile,
} from "@/lib/profile"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/cloud-agents")({
  component: CloudAgentsPage,
})

function CloudAgentsPage() {
  const session = useSession()
  const profile = useProfile()
  const options = useOptions()
  const repos = useRepos()
  const save = useSaveProfile()

  const [modelId, setModelId] = useState("")
  const [effort, setEffort] = useState("")
  const [subagentModelId, setSubagentModelId] = useState("")
  const [subagentEffort, setSubagentEffort] = useState("")
  const [defaultRepo, setDefaultRepo] = useState("")
  const [baseBranch, setBaseBranch] = useState("")
  const [branchPrefix, setBranchPrefix] = useState("")
  const [error, setError] = useState<string | null>(null)
  const initialized = useRef(false)

  const firstModel: ModelOption | undefined = options.data?.models[0]
  const currentModel: ModelOption | undefined =
    options.data?.models.find((m) => m.id === modelId) ?? firstModel
  const currentSubagentModel: ModelOption | undefined =
    options.data?.models.find((m) => m.id === subagentModelId) ?? firstModel

  useEffect(() => {
    if (!profile.data || initialized.current) return
    // For users with no saved profile, wait until the options API has loaded
    // so the model/effort selects can initialise to the first available option.
    const hasModel = !!profile.data.default_model || !!firstModel
    if (!hasModel) return
    initialized.current = true
    setModelId(profile.data.default_model ?? firstModel?.id ?? "")
    setEffort(profile.data.reasoning_effort ?? firstModel?.default_effort ?? "")
    setSubagentModelId(
      profile.data.default_subagent_model ??
        profile.data.default_model ??
        firstModel?.id ??
        ""
    )
    setSubagentEffort(
      profile.data.subagent_reasoning_effort ??
        profile.data.reasoning_effort ??
        firstModel?.default_effort ??
        ""
    )
    setDefaultRepo(profile.data.default_repo ?? "")
    setBaseBranch(profile.data.base_branch ?? "")
    setBranchPrefix(profile.data.branch_prefix ?? "")
  }, [profile.data, firstModel?.id, firstModel?.default_effort, firstModel])

  useEffect(() => {
    if (currentModel && !currentModel.efforts.includes(effort)) {
      setEffort(currentModel.default_effort)
    }
  }, [currentModel, effort])

  useEffect(() => {
    if (
      currentSubagentModel &&
      !currentSubagentModel.efforts.includes(subagentEffort)
    ) {
      setSubagentEffort(currentSubagentModel.default_effort)
    }
  }, [currentSubagentModel, subagentEffort])

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session.data) return <Navigate to="/login" />

  const fallbackModel = firstModel?.id ?? ""
  const fallbackEffort = firstModel?.default_effort ?? ""

  const persist = (patch: Parameters<typeof buildProfileUpdate>[1]) => {
    setError(null)
    save
      .mutateAsync(
        buildProfileUpdate(profile.data, patch, fallbackModel, fallbackEffort)
      )
      .catch((e: Error) => setError(e.message))
  }

  const persistDefaults = () => {
    persist({
      default_model: modelId,
      reasoning_effort: effort,
      default_subagent_model: subagentModelId,
      subagent_reasoning_effort: subagentEffort,
      default_repo: defaultRepo || null,
      base_branch: baseBranch || null,
      branch_prefix: branchPrefix || null,
    })
  }

  return (
    <AppShell
      user={session.data}
      title="Open SWE Agent"
      description="Configure how the Open SWE Agent picks a model, repository, and PR defaults."
    >
      <SettingsSection title="Defaults">
        <div className="divide-y divide-border">
          <SettingsRow
            label="Default Model"
            description="Used when no model is specified"
            control={
              <Select value={modelId} onValueChange={(v) => v && setModelId(v)}>
                <SelectTrigger className="w-40">
                  <SelectValue placeholder="Pick a model" />
                </SelectTrigger>
                <SelectContent>
                  {options.data?.models.map((m) => (
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
              <Select value={effort} onValueChange={(v) => v && setEffort(v)}>
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
            description="Used for delegated tasks launched by your agent"
            control={
              <Select
                value={subagentModelId}
                onValueChange={(v) => v && setSubagentModelId(v)}
              >
                <SelectTrigger className="w-40">
                  <SelectValue placeholder="Pick a model" />
                </SelectTrigger>
                <SelectContent>
                  {options.data?.models.map((m) => (
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
              <div className="w-64">
                {repos.data && repos.data.repositories.length > 0 ? (
                  <Combobox
                    items={repos.data.repositories.map((r) => r.full_name)}
                    value={defaultRepo}
                    onValueChange={(v) =>
                      setDefaultRepo(typeof v === "string" ? v : "")
                    }
                  >
                    <ComboboxInput
                      placeholder="Pick a repository…"
                      showClear
                      className="w-full"
                    />
                    <ComboboxContent className="min-w-[var(--anchor-width)]">
                      <ComboboxList className="max-h-64">
                        <ComboboxEmpty>No matches</ComboboxEmpty>
                        {repos.data.repositories.map((r) => (
                          <ComboboxItem key={r.full_name} value={r.full_name}>
                            <span className="truncate">{r.full_name}</span>
                          </ComboboxItem>
                        ))}
                      </ComboboxList>
                    </ComboboxContent>
                  </Combobox>
                ) : (
                  <Input
                    placeholder="owner/repo"
                    value={defaultRepo}
                    onChange={(e) => setDefaultRepo(e.target.value)}
                  />
                )}
              </div>
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
            description="Agent will attempt to fix failing CI checks on PRs it opens."
            comingSoon
            control={
              <Switch
                checked={profile.data?.auto_fix_ci ?? true}
                onCheckedChange={(v) => persist({ auto_fix_ci: v })}
                disabled
              />
            }
          />
          <SettingsRow
            label="Always Create PRs"
            description="Always create a pull request for code changes. When disabled, agents create PRs only when necessary or requested."
            control={
              <Switch
                checked={profile.data?.create_prs ?? false}
                onCheckedChange={(v) => persist({ create_prs: v })}
              />
            }
          />
        </div>
      </SettingsSection>

      {error && <p className="text-xs text-destructive">{error}</p>}
    </AppShell>
  )
}
