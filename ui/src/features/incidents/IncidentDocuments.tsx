import { useQuery } from "@tanstack/react-query"
import { useState } from "react"

import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"

import { Copy, Info } from "@/components/glyphs"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { invalidationTopic } from "@/lib/invalidations/topics"
import { ErrorState, isReadUnavailable } from "./shared"
import { workspaceApi } from "./workspace-api"

export function IncidentDocuments({ incidentId }: { incidentId: string }) {
  const [copyNotice, setCopyNotice] = useState("")
  const documents = useQuery({
    queryKey: ["incidents", "documents", incidentId, "current"],
    queryFn: () => workspaceApi.documents(incidentId),
    meta: {
      invalidatedBy: [
        invalidationTopic("incidents", incidentId),
        invalidationTopic("incident-settings"),
      ],
    },
    retry: false,
  })
  if (documents.isPending)
    return (
      <Inline role="status" gap="sm" className="text-body text-ink-subtle">
        <Spinner size="sm" />
        Loading incident summary…
      </Inline>
    )
  if (
    documents.error &&
    (!documents.data || !isReadUnavailable(documents.error))
  )
    return (
      <ErrorState
        error={documents.error}
        retry={() => void documents.refetch()}
      />
    )
  const markdown = documents.data?.postmortem?.markdown ?? ""
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(markdown)
      setCopyNotice("Incident copied as Markdown.")
    } catch (error) {
      console.warn("Could not copy incident", error)
      setCopyNotice(
        "Unable to copy. Select the incident text to copy it manually."
      )
    }
  }
  return (
    <PageSection
      title="Postmortem summary"
      contained
      inset="padded"
      actions={
        <Button
          size="compact"
          variant="outline"
          disabled={!markdown.trim()}
          onClick={() => void copy()}
        >
          <Icon icon={Copy} size="sm" />
          Copy incident
        </Button>
      }
    >
      {documents.error && (
        <StateNotice
          tone="INFO"
          icon={Info}
          title="Showing the last loaded summary"
          description={documents.error.message}
        />
      )}
      {markdown ? (
        <Box className="mx-auto w-full max-w-reading py-4">
          <Markdown content={markdown} />
        </Box>
      ) : (
        <Box render={<p />} className="text-body text-ink-subtle">
          The agent will add a summary after investigating.
        </Box>
      )}
      {copyNotice && (
        <Box render={<p />} role="status" className="text-label text-ink">
          {copyNotice}
        </Box>
      )}
    </PageSection>
  )
}
