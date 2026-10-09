import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Skeleton } from "@langchain/macaw-components/Skeleton"

import { SettingsPanel, SettingsSection } from "@/components/AppShell"
import { InstructionsEditor } from "@/components/InstructionsEditor"
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
    <SettingsSection title="Personal instructions">
      <SettingsPanel>
        {instructions.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : instructions.isError ? (
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
        ) : (
          <>
            <InstructionsEditor
              value={value}
              onChange={setDraft}
              disabled={mutating}
              placeholder="e.g. Always run the linter before pushing. Prefer terse Slack updates."
            />
            <div className="flex flex-wrap items-center gap-space-2">
              <Button
                size="xs"
                color="primary"
                disabled={!dirty || mutating}
                onClick={() => save.mutate(value)}
              >
                Save instructions
              </Button>
              {dirty && (
                <span className="text-xs text-secondary">Unsaved changes</span>
              )}
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                className="ml-auto"
                disabled={mutating || (!saved && !dirty)}
                onClick={() => setConfirmingClear(true)}
              >
                Clear
              </Button>
            </div>
            {error && <p className="text-xs text-error-secondary">{error}</p>}
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
          </>
        )}
      </SettingsPanel>
    </SettingsSection>
  )
}
