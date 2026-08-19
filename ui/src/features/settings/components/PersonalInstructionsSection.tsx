import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { InstructionsEditor } from "@/components/InstructionsEditor"
import { Skeleton } from "@/components/langsmith"
import { Button } from "@/components/ui/button"
import {
  SettingsPanel,
  SettingsSection,
} from "@/features/settings/components/SettingsPrimitives"
import { api } from "@/lib/api"

export function PersonalInstructionsSection() {
  const qc = useQueryClient()
  const instructions = useQuery({
    queryKey: ["myInstructions"],
    queryFn: api.getMyInstructions,
  })
  const [draft, setDraft] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const saved = instructions.data?.instructions ?? ""
  const value = draft ?? saved
  const dirty = draft !== null && draft !== saved

  const onSuccess = () => {
    void qc.invalidateQueries({ queryKey: ["myInstructions"] })
    setDraft(null)
    setError(null)
  }
  const onError = (e: Error) => setError(e.message)

  const save = useMutation({
    mutationFn: (next: string) => api.saveMyInstructions(next),
    onSuccess,
    onError,
  })
  const clear = useMutation({
    mutationFn: () => api.deleteMyInstructions(),
    onSuccess,
    onError,
  })
  const mutating = save.isPending || clear.isPending

  const onClear = () => {
    if (
      !window.confirm(
        "Clear your personal instructions? This cannot be undone."
      )
    ) {
      return
    }
    void clear.mutateAsync()
  }

  return (
    <SettingsSection
      title="My instructions"
      description="Standing instructions appended to the coding agent's system prompt for every run you trigger, on any surface. Repository instructions and AGENTS.md win when they conflict."
    >
      <SettingsPanel>
        {instructions.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : instructions.isError ? (
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs text-destructive">
              Could not load your instructions:{" "}
              {instructions.error instanceof Error
                ? instructions.error.message
                : "unknown error"}
              . Editing is disabled so a failed load can't overwrite them.
            </p>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void instructions.refetch()}
            >
              Retry
            </Button>
          </div>
        ) : (
          <>
            <InstructionsEditor
              value={value}
              onChange={setDraft}
              disabled={mutating}
              placeholder="e.g. Always run the linter before pushing. Prefer terse Slack updates."
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                disabled={!dirty || mutating}
                onClick={() => void save.mutateAsync(value)}
              >
                Save instructions
              </Button>
              {dirty && (
                <span className="text-xs text-muted-foreground">
                  Unsaved changes
                </span>
              )}
              <Button
                size="sm"
                variant="outline"
                className="ml-auto"
                disabled={mutating || (!saved && !dirty)}
                onClick={onClear}
              >
                Clear
              </Button>
            </div>
            {error && <p className="text-xs text-destructive">{error}</p>}
          </>
        )}
      </SettingsPanel>
    </SettingsSection>
  )
}
