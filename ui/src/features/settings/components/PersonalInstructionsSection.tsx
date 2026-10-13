import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Skeleton } from "@langchain/macaw-components/Skeleton"

import { EditorScreen } from "@/components/EditorScreen"
import { TextEditor } from "@/components/TextEditor"
import { api, type UserInstructions } from "@/lib/api"
import { ConfirmDialog } from "./ConfirmDialog"

export function PersonalInstructionsSection() {
  const qc = useQueryClient()
  const instructions = useQuery({
    queryKey: ["myInstructions"],
    queryFn: api.getMyInstructions,
  })
  const [draft, setDraft] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmingClear, setConfirmingClear] = useState(false)

  const saved = instructions.data?.instructions ?? ""
  const value = draft ?? saved
  const dirty = draft !== null && draft !== saved
  const ready = instructions.isSuccess

  const onSuccess = (text: string) => {
    qc.setQueryData<UserInstructions>(["myInstructions"], (current) => ({
      ...current,
      instructions: text,
    }))
    void qc.invalidateQueries({ queryKey: ["myInstructions"] })
    setDraft(null)
    setError(null)
  }

  const save = useMutation({
    meta: { silent: true },
    mutationFn: (next: string) => api.saveMyInstructions(next),
    onSuccess: (record) => onSuccess(record.instructions),
    onError: (e: Error) => setError(e.message),
  })
  const clear = useMutation({
    meta: { errorTitle: "Couldn't clear instructions" },
    mutationFn: () => api.deleteMyInstructions(),
    onSuccess: () => onSuccess(""),
    onSettled: () => setConfirmingClear(false),
  })
  const mutating = save.isPending || clear.isPending

  return (
    <EditorScreen
      title="Instructions"
      description="Standing instructions added to the agent's system prompt for every run you trigger, on any surface. Repository instructions and AGENTS.md win when they conflict."
      actions={
        ready && (
          <>
            {error && (
              <span className="text-xs text-error-secondary">{error}</span>
            )}
            {dirty && (
              <span className="text-xs text-secondary">Unsaved changes</span>
            )}
            <Button
              size="xs"
              color="secondary"
              variant="outlined"
              disabled={mutating || (!saved && !dirty)}
              onClick={() => setConfirmingClear(true)}
            >
              Clear
            </Button>
            <Button
              size="xs"
              color="primary"
              disabled={!dirty || mutating}
              onClick={() => save.mutate(value)}
            >
              Save instructions
            </Button>
          </>
        )
      }
    >
      {instructions.isLoading ? (
        <Skeleton className="m-space-5 flex-1" />
      ) : instructions.isError ? (
        <div className="p-space-5">
          <Banner
            intent="error"
            title="Could not load your instructions."
            action={
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                onClick={() => void instructions.refetch()}
              >
                Retry
              </Button>
            }
          >
            {instructions.error instanceof Error
              ? instructions.error.message
              : "Unknown error"}
            . Editing is disabled so a failed load can&apos;t overwrite them.
          </Banner>
        </div>
      ) : (
        <TextEditor
          value={value}
          onChange={setDraft}
          ariaLabel="Personal instructions"
          disabled={mutating}
          placeholder="e.g. Always run the linter before pushing. Prefer terse Slack updates."
        />
      )}
      <ConfirmDialog
        open={confirmingClear}
        onOpenChange={setConfirmingClear}
        title="Clear your personal instructions?"
        description="This cannot be undone."
        confirmLabel="Clear instructions"
        pendingLabel="Clearing…"
        pending={clear.isPending}
        destructive
        onConfirm={() => clear.mutate()}
      />
    </EditorScreen>
  )
}
