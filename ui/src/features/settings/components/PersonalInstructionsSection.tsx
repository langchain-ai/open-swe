import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { ConfirmableAction } from "@langchain/gtm-platform-design-system/patterns/confirmable-action"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { AlertTriangle, RotateCcw } from "@/components/glyphs"
import { InstructionsEditor } from "@/components/InstructionsEditor"
import { api, type UserInstructions } from "@/lib/api"

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
  // The confirmation dialog reports a failure inline, so the toast is suppressed.
  const clear = useMutation({
    meta: { errorTitle: "Couldn't clear instructions", silent: true },
    mutationFn: () => api.deleteMyInstructions(),
    onSuccess: () => onSuccess(""),
  })
  const mutating = save.isPending || clear.isPending

  return (
    <PageSection
      title="Personal instructions"
      description="Applied on every surface. Repository instructions and AGENTS.md win when they conflict."
      contained
      inset="padded"
    >
      {instructions.isLoading ? (
        <Skeleton className="h-90 w-full rounded-compact" />
      ) : instructions.isError ? (
        <StateNotice
          tone="RISK"
          icon={AlertTriangle}
          title="Couldn't load your instructions"
          description={`${
            instructions.error instanceof Error
              ? instructions.error.message
              : "Unknown error"
          }. Editing is disabled so a failed load can't overwrite them.`}
          action={
            <Button
              size="compact"
              variant="outline"
              onClick={() => void instructions.refetch()}
            >
              <Icon icon={RotateCcw} size="sm" />
              Retry
            </Button>
          }
        />
      ) : (
        <Stack gap="md">
          <InstructionsEditor
            value={value}
            onChange={setDraft}
            disabled={mutating}
            placeholder="e.g. Always run the linter before pushing. Prefer terse Slack updates."
          />
          <Inline gap="sm" align="center" justify="between" wrap>
            <Inline gap="sm" align="center">
              <Button
                size="compact"
                disabled={!dirty || mutating}
                loading={save.isPending}
                onClick={() => save.mutate(value)}
              >
                Save instructions
              </Button>
              {dirty && (
                <Box render={<span />} className="text-meta text-ink-subtle">
                  Unsaved changes
                </Box>
              )}
            </Inline>
            <ConfirmableAction
              title="Clear your personal instructions?"
              description="Your standing instructions are deleted and future runs stop using them. This cannot be undone."
              confirmLabel="Clear instructions"
              onConfirm={async () => {
                await clear.mutateAsync()
              }}
              trigger={
                <Button
                  size="compact"
                  variant="outline"
                  disabled={mutating || (!saved && !dirty)}
                >
                  Clear
                </Button>
              }
            />
          </Inline>
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
      )}
    </PageSection>
  )
}
