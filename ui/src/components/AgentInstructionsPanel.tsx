import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import type { FormEvent } from "react"

import { ConfirmableAction } from "@langchain/gtm-platform-design-system/patterns/confirmable-action"
import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { FormField } from "@langchain/gtm-platform-design-system/patterns/form-field"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button, buttonVariants } from "@langchain/gtm-platform-design-system/ui/button"
import { Combobox, ComboboxContent, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList, ComboboxTrigger } from "@langchain/gtm-platform-design-system/ui/combobox"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { AlertTriangle, ChevronRight, FileText, GitHub, Lock } from "@/components/glyphs"
import { InstructionsEditor } from "@/components/InstructionsEditor"
import {
  api,
  isGithubReauthError,
  loginUrl,
  type AgentInstructions,
} from "@/lib/api"
import { useRepos } from "@/lib/profile"
import { normalizeRepoFullName } from "@/lib/repo"

const REPO_ROW_CLASS =
  "h-row-data w-full px-5 text-left text-label text-ink transition-colors duration-fast ease-out-quint outline-none hover:bg-hover focus-visible:bg-hover aria-pressed:bg-selected aria-pressed:font-medium motion-reduce:transition-none"

function formatMutationError(e: Error): string {
  return isGithubReauthError(e)
    ? "GitHub token expired — sign in again using the link above."
    : e.message
}

export function AgentInstructionsPanel() {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [addRepo, setAddRepo] = useState("")
  const [selected, setSelected] = useState<string | null>(null)
  const [draft, setDraft] = useState("")

  const instructions = useQuery({
    queryKey: ["agentInstructions"],
    queryFn: api.listAgentInstructions,
  })

  const repos = useRepos()

  const detail = useQuery({
    queryKey: ["agentInstruction", selected],
    queryFn: () => api.getAgentInstructions(selected!),
    enabled: !!selected,
  })

  const loadedRepo = detail.data?.full_name
  const loadedInstructions = detail.data?.instructions
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    if (loadedInstructions !== undefined) setDraft(loadedInstructions)
  }, [loadedInstructions, loadedRepo])

  const create = useMutation({
    meta: { silent: true },
    mutationFn: (full_name: string) => api.createAgentInstructions(full_name),
    onSuccess: (record) => {
      void qc.invalidateQueries({ queryKey: ["agentInstructions"] })
      setSelected(record.full_name)
      setError(null)
    },
    onError: (e: Error) => setError(formatMutationError(e)),
  })

  const save = useMutation({
    meta: { silent: true },
    mutationFn: ({ full_name, value }: { full_name: string; value: string }) =>
      api.saveAgentInstructions(full_name, value),
    onSuccess: (_saved, { full_name }) => {
      void qc.invalidateQueries({ queryKey: ["agentInstructions"] })
      void qc.invalidateQueries({ queryKey: ["agentInstruction", full_name] })
      setError(null)
    },
    onError: (e: Error) => setError(formatMutationError(e)),
  })

  const remove = useMutation({
    meta: { errorTitle: "Couldn't remove repository instructions" },
    mutationFn: (full_name: string) => api.deleteAgentInstructions(full_name),
    onMutate: async (full_name) => {
      await qc.cancelQueries({ queryKey: ["agentInstructions"] })
      const removed = qc
        .getQueryData<Array<AgentInstructions>>(["agentInstructions"])
        ?.find((s) => s.full_name === full_name)
      qc.setQueryData<Array<AgentInstructions>>(
        ["agentInstructions"],
        (current) => current?.filter((s) => s.full_name !== full_name)
      )
      const wasSelected = selected === full_name
      if (wasSelected) {
        setSelected(null)
        setDraft("")
      }
      return { removed, wasSelected }
    },
    onError: (_e, full_name, context) => {
      if (context?.wasSelected) setSelected(full_name)
      const removed = context?.removed
      if (removed)
        qc.setQueryData<Array<AgentInstructions>>(
          ["agentInstructions"],
          (current) =>
            current && !current.some((s) => s.full_name === removed.full_name)
              ? [...current, removed]
              : current
        )
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["agentInstructions"] }),
  })

  if (instructions.isLoading) {
    return <Skeleton className="h-40 w-full rounded-panel" />
  }

  const configured = new Set((instructions.data ?? []).map((s) => s.full_name))
  const suggestedRepos = (repos.data?.repositories ?? []).filter(
    (r) => !configured.has(r.full_name)
  )
  const normalizedAddRepo = normalizeRepoFullName(addRepo)
  const canAdd =
    normalizedAddRepo !== null && !configured.has(normalizedAddRepo)
  const active =
    detail.data ??
    instructions.data?.find((s) => s.full_name === selected) ??
    null
  const dirty = active != null && draft !== active.instructions

  const handleAdd = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!normalizedAddRepo || !canAdd) return
    create.mutate(normalizedAddRepo, { onSuccess: () => setAddRepo("") })
  }

  const githubReauth =
    (repos.isError && isGithubReauthError(repos.error)) ||
    (error !== null && /github token|re-login required/i.test(error))
  const configuredList = instructions.data ?? []

  return (
    <Stack gap="xl">
      {githubReauth && (
        <StateNotice
          tone="ATTENTION"
          icon={GitHub}
          title="Your GitHub connection expired"
          description="Sign in again to list installed repos."
          action={
            <a
              href={loginUrl()}
              className={buttonVariants({ size: "compact", variant: "outline" })}
            >
              Sign in with GitHub again
            </a>
          }
        />
      )}

      <PageSection title="Repositories" contained>
        <Stack
          render={<form onSubmit={handleAdd} />}
          gap="md"
          padding="lg"
          className="border-b border-line"
        >
          <Inline gap="sm" align="end">
            <Stack grow>
              <FormField
                label="Add repository"
                control={
                  <Input
                    placeholder="owner/repo"
                    value={addRepo}
                    onChange={(e) => setAddRepo(e.target.value)}
                  />
                }
              />
            </Stack>
            <Button
              type="submit"
              disabled={!canAdd || create.isPending}
              loading={create.isPending}
            >
              Add
            </Button>
          </Inline>
          {suggestedRepos.length > 0 && (
            <Combobox value={addRepo} onValueChange={setAddRepo}>
              <ComboboxTrigger placeholder="Search installed repos…">
                {suggestedRepos.some((r) => r.full_name === addRepo)
                  ? addRepo
                  : undefined}
              </ComboboxTrigger>
              <ComboboxContent>
                <ComboboxInput placeholder="Search installed repos…" />
                <ComboboxList>
                  <ComboboxEmpty>No matches</ComboboxEmpty>
                  {suggestedRepos.map((r) => (
                    <ComboboxItem key={r.full_name} value={r.full_name}>
                      <Box render={<span />} className="truncate" title={r.full_name}>
                        {r.full_name}
                      </Box>
                      {r.private && (
                        <Icon
                          icon={Lock}
                          size="sm"
                          label="Private"
                          className="ml-auto text-ink-subtle"
                        />
                      )}
                    </ComboboxItem>
                  ))}
                </ComboboxList>
              </ComboboxContent>
            </Combobox>
          )}
        </Stack>
        {configuredList.length === 0 ? (
          <EmptyState
            icon={FileText}
            title="No repositories yet"
            description="Add a repository to give its runs custom instructions."
          />
        ) : (
          <Stack render={<ul />} gap="none" aria-label="Repositories">
            {configuredList.map((s) => (
              <Box
                key={s.full_name}
                render={<li />}
                className="border-b border-line last:border-b-0"
              >
                <Inline
                  render={
                    <button
                      type="button"
                      aria-pressed={selected === s.full_name}
                      onClick={() => setSelected(s.full_name)}
                    />
                  }
                  gap="sm"
                  align="center"
                  justify="between"
                  className={REPO_ROW_CLASS}
                >
                  <Box render={<span />} className="truncate font-mono">
                    {s.full_name}
                  </Box>
                  <Icon
                    icon={ChevronRight}
                    size="sm"
                    className="shrink-0 text-ink-subtle"
                  />
                </Inline>
              </Box>
            ))}
          </Stack>
        )}
      </PageSection>

      {!selected || !active ? (
        configuredList.length === 0 ? null : (
          <Box render={<p />} className="text-label text-ink-subtle">
            Select a repository above to view or edit its custom agent
            instructions.
          </Box>
        )
      ) : (
        <PageSection
          title={active.full_name}
          contained
          inset="padded"
          actions={
            <ConfirmableAction
              title={`Remove custom instructions for ${active.full_name}?`}
              description="Runs in this repository stop receiving these instructions. This cannot be undone."
              confirmLabel="Remove instructions"
              onConfirm={async () => {
                // Removal is optimistic: the dialog closes at once and a
                // failure restores the repository with a toast.
                remove.mutate(active.full_name)
              }}
              trigger={
                <Button size="compact" variant="outline">
                  Remove
                </Button>
              }
            />
          }
        >
          <Stack gap="md">
            <InstructionsEditor
              value={draft}
              onChange={setDraft}
              placeholder="Write custom instructions for the coding agent on this repository (markdown)."
            />
            <Inline gap="sm" align="center">
              <Button
                size="compact"
                disabled={!dirty || save.isPending}
                loading={save.isPending}
                onClick={() =>
                  save.mutate({
                    full_name: active.full_name,
                    value: draft,
                  })
                }
              >
                Save instructions
              </Button>
              {dirty && (
                <Box render={<span />} className="text-meta text-ink-subtle">
                  Unsaved changes
                </Box>
              )}
            </Inline>
          </Stack>
        </PageSection>
      )}

      {error && (
        <Inline
          role="alert"
          gap="sm"
          align="start"
          className="text-label text-risk"
        >
          <Icon icon={AlertTriangle} size="sm" />
          <Box render={<span />}>{error}</Box>
        </Inline>
      )}
    </Stack>
  )
}
