import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { Copy } from "lucide-react"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
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
      <p role="status" className="text-body text-ink-subtle">
        Loading incident summary…
      </p>
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
    } catch {
      setCopyNotice(
        "Unable to copy. Select the incident text to copy it manually."
      )
    }
  }
  return (
    <section className="space-y-4 rounded-control border border-line bg-panel p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-body font-medium">Postmortem summary</h2>
        <Button
          size="compact"
          variant="outline"
          disabled={!markdown.trim()}
          onClick={() => void copy()}
        >
          <Copy className="size-3.5" />
          Copy incident
        </Button>
      </div>
      {documents.error && (
        <p role="alert" className="text-body text-attention">
          {documents.error.message}
        </p>
      )}
      {markdown ? (
        <div className="mx-auto max-w-3xl py-5 sm:px-4">
          <Markdown content={markdown} />
        </div>
      ) : (
        <p className="text-body text-ink-subtle">
          The agent will add a summary after investigating.
        </p>
      )}
      {copyNotice && (
        <p role="status" className="text-label">
          {copyNotice}
        </p>
      )}
    </section>
  )
}
