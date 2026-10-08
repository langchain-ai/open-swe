import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { AddRepositoryField } from "@/components/AddRepositoryField"
import { InstructionsEditor } from "@/components/InstructionsEditor"
import {
  api,
  isGithubReauthError,
  loginUrl,
  type AgentInstructions,
} from "@/lib/api"
import { useRepos } from "@/lib/profile"
import { normalizeRepoFullName } from "@/lib/repo"
import { cn } from "@/lib/utils"

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
    return <Skeleton className="h-40" />
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

  const handleAdd = () => {
    if (!normalizedAddRepo || !canAdd) return
    create.mutate(normalizedAddRepo, { onSuccess: () => setAddRepo("") })
  }

  const githubReauth =
    (repos.isError && isGithubReauthError(repos.error)) ||
    (error !== null && /github token|re-login required/i.test(error))

  return (
    <div className="flex flex-col gap-space-5 p-space-4">
      {githubReauth && (
        <Banner intent="error">
          <span className="text-xs text-error-secondary">
            Your GitHub connection expired.{" "}
            <a
              href={loginUrl()}
              className="font-medium underline underline-offset-2"
            >
              Sign in with GitHub again
            </a>{" "}
            to list installed repos.
          </span>
        </Banner>
      )}
      <section className="space-y-4">
        <AddRepositoryField
          id="add-instruction-repo"
          value={addRepo}
          onChange={setAddRepo}
          suggestions={suggestedRepos}
          canAdd={canAdd && !create.isPending}
          onAdd={handleAdd}
        />

        <div className="space-y-2">
          <p className="text-xs font-medium text-primary">Repositories</p>
          {(instructions.data ?? []).length === 0 ? (
            <p className="text-xs text-secondary">No repositories yet.</p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {(instructions.data ?? []).map((s) => (
                <li key={s.full_name}>
                  <button
                    type="button"
                    aria-pressed={selected === s.full_name}
                    className={cn(
                      "inline-flex max-w-full items-center gap-space-2 rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-surface-level-1-hover",
                      selected === s.full_name
                        ? "border-brand bg-selected font-medium"
                        : "border-default"
                    )}
                    onClick={() => setSelected(s.full_name)}
                  >
                    <span className="truncate">{s.full_name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <div className="border-t border-default" />

      <section className="space-y-3">
        {!selected || !active ? (
          <p className="text-xs text-secondary">
            Select a repository above to view or edit its custom agent
            instructions.
          </p>
        ) : (
          <>
            <p className="text-sm font-medium text-primary">
              {active.full_name}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                color="primary"
                size="xs"
                disabled={!dirty || save.isPending}
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
                <span className="self-center text-xs text-secondary">
                  Unsaved changes
                </span>
              )}
              <Button
                color="error"
                size="xs"
                className="ml-auto"
                onClick={() => {
                  if (
                    !window.confirm(
                      `Remove custom instructions for ${active.full_name}? This cannot be undone.`
                    )
                  ) {
                    return
                  }
                  remove.mutate(active.full_name)
                }}
              >
                Remove
              </Button>
            </div>
            <InstructionsEditor
              value={draft}
              onChange={setDraft}
              placeholder="Write custom instructions for the coding agent on this repository (markdown)."
            />
          </>
        )}
        {error && <p className="text-xs text-error-secondary">{error}</p>}
      </section>
    </div>
  )
}
