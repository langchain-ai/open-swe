import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  ErrorState,
  ExternalLink,
  formatTime,
  humanize,
  isReadUnavailable,
} from "./shared"
import { workspaceApi } from "./workspace-api"
import type {
  IncidentDocuments as DocumentPayload,
  DocumentKind,
  DocumentOperation,
  DocumentRevision,
} from "./workspace-api"

function DocumentEditor({
  incidentId,
  kind,
  current,
  canEdit,
  operations,
}: {
  incidentId: string
  kind: DocumentKind
  current: DocumentRevision | null
  canEdit: boolean
  operations: DocumentOperation[]
}) {
  const client = useQueryClient()
  const title = kind === "postmortem" ? "Postmortem" : "Status-page draft"
  const [draft, setDraft] = useState<{ markdown: string; base: number } | null>(
    null
  )
  const [showHistory, setShowHistory] = useState(false)
  const [copyNotice, setCopyNotice] = useState("")
  const identity = useRef<{ key: string; id: string } | null>(null)
  const value = draft?.markdown ?? current?.markdown ?? ""
  const save = useMutation({
    mutationFn: ({ markdown, base }: { markdown: string; base: number }) => {
      const key = JSON.stringify({ markdown, base })
      if (identity.current?.key !== key)
        identity.current = { key, id: crypto.randomUUID() }
      return workspaceApi.saveDocument(
        incidentId,
        kind,
        markdown,
        base,
        identity.current.id
      )
    },
    onSuccess: () => {
      identity.current = null
    },
  })
  const operation = useQuery({
    queryKey: [
      "incidents",
      "documents",
      incidentId,
      "operation",
      save.data?.id,
    ],
    queryFn: () => workspaceApi.documentOperation(incidentId, save.data!.id),
    enabled: Boolean(save.data),
    refetchInterval: (query) =>
      query.state.data?.status === "pending" || !query.state.data
        ? 2000
        : false,
    retry: false,
  })
  const outcome = operation.data ?? save.data
  const pending = save.isPending || outcome?.status === "pending"
  useEffect(() => {
    if (outcome?.status === "applied") {
      void client
        .invalidateQueries({
          queryKey: ["incidents", "documents", incidentId, "current"],
        })
        .then(() =>
          setDraft((edit) =>
            client.getQueryData<DocumentPayload>([
              "incidents",
              "documents",
              incidentId,
              "current",
            ])?.[kind]?.revision === outcome.revision &&
            edit?.markdown === save.variables?.markdown &&
            edit?.base === save.variables?.base
              ? null
              : edit
          )
        )
      void client.invalidateQueries({
        queryKey: ["incidents", "documents", incidentId, "revisions", kind],
      })
    } else if (outcome?.status === "conflict") {
      void client.invalidateQueries({
        queryKey: ["incidents", "documents", incidentId, "current"],
      })
    }
  }, [
    outcome?.id,
    outcome?.status,
    outcome?.revision,
    client,
    incidentId,
    kind,
    save.variables,
  ])
  const revisions = useQuery({
    queryKey: ["incidents", "documents", incidentId, "revisions", kind],
    queryFn: () => workspaceApi.revisions(incidentId, kind),
    enabled: showHistory,
    retry: false,
  })
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopyNotice("Draft copied.")
    } catch {
      setCopyNotice(
        "Unable to copy. Select the draft text to copy it manually."
      )
    }
  }
  return (
    <section className="space-y-4 rounded-xl border border-border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium">{title}</h2>
        <span className="text-xs text-muted-foreground">
          {current
            ? `Revision ${current.revision} · ${current.author} · ${formatTime(current.created_at)}`
            : "No saved revision"}
        </span>
      </div>
      {kind === "status_page_draft" && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          Prepare a customer-facing update here. Publishing to a status page is
          unsupported; copy the draft into your status page.
        </p>
      )}
      {canEdit ? (
        <Textarea
          aria-label={title}
          value={value}
          maxLength={200000}
          disabled={pending}
          className={kind === "postmortem" ? "min-h-64" : "min-h-32"}
          placeholder={
            kind === "postmortem"
              ? "Summary, impact, timeline, cause, mitigation, resolution, follow-ups, and evidence…"
              : "Customer impact, affected services, and the next update…"
          }
          onChange={(event) =>
            setDraft({
              markdown: event.target.value,
              base: draft?.base ?? current?.revision ?? 0,
            })
          }
        />
      ) : (
        <p className="text-sm leading-7 whitespace-pre-wrap">
          {value || "No document yet."}
        </p>
      )}
      {current?.evidence?.length ? (
        <ul className="space-y-2 text-xs">
          {current.evidence.map((evidence) => (
            <li key={evidence.id}>
              {evidence.available ? (
                <ExternalLink href={evidence.url}>
                  {evidence.source}
                </ExternalLink>
              ) : (
                `${evidence.source}: evidence unavailable`
              )}
            </li>
          ))}
        </ul>
      ) : null}
      {(save.error || operation.error) && (
        <p role="alert" className="text-sm text-destructive-foreground">
          {(save.error ?? operation.error)?.message}
        </p>
      )}
      {outcome && (
        <p
          role={
            outcome.status === "conflict" || outcome.status === "rejected"
              ? "alert"
              : "status"
          }
          className="text-sm"
        >
          {outcome.status === "pending"
            ? "Save pending. Your edit will appear once it is applied."
            : outcome.status === "applied"
              ? `Saved revision ${outcome.revision}.`
              : outcome.error ||
                "This edit could not be applied. Your text is preserved."}
        </p>
      )}
      {draft && current && draft.base !== current.revision && (
        <div className="space-y-2 rounded-lg bg-muted p-3 text-sm">
          <p>A newer revision is available. Your unsaved edit is preserved.</p>
          <details>
            <summary className="cursor-pointer">
              Read saved revision {current.revision}
            </summary>
            <p className="mt-3 whitespace-pre-wrap">{current.markdown}</p>
          </details>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setDraft(null)
              save.reset()
            }}
          >
            Discard edit and load latest
          </Button>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {canEdit && (
          <Button
            size="sm"
            disabled={
              pending ||
              !draft ||
              Boolean(current && draft.base !== current.revision)
            }
            onClick={() =>
              save.mutate({
                markdown: value,
                base: draft?.base ?? current?.revision ?? 0,
              })
            }
          >
            {kind === "postmortem" ? "Save postmortem" : "Save draft"}
          </Button>
        )}
        {kind === "status_page_draft" && (
          <Button
            size="sm"
            variant="outline"
            disabled={!value}
            onClick={() => void copy()}
          >
            Copy draft
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={showHistory}
          onClick={() => setShowHistory(!showHistory)}
        >
          Revision history
        </Button>
      </div>
      {copyNotice && (
        <p role="status" className="text-xs">
          {copyNotice}
        </p>
      )}
      {showHistory && (
        <div className="space-y-3 border-t border-border pt-4">
          {revisions.isPending ? (
            <p role="status" className="text-xs">
              Loading revisions…
            </p>
          ) : revisions.error ? (
            <ErrorState
              error={revisions.error}
              retry={() => void revisions.refetch()}
            />
          ) : !revisions.data.items.length ? (
            <p className="text-xs text-muted-foreground">No revisions yet.</p>
          ) : (
            revisions.data.items.map((revision) => (
              <details key={revision.revision}>
                <summary className="cursor-pointer text-xs">
                  Revision {revision.revision} · {revision.author} ·{" "}
                  {formatTime(revision.created_at)} ·{" "}
                  {humanize(revision.source)}
                </summary>
                <p className="mt-3 text-sm leading-7 whitespace-pre-wrap">
                  {revision.markdown}
                </p>
              </details>
            ))
          )}
          {operations
            .filter((item) => item.kind === kind)
            .map((item) => (
              <p key={item.id} className="text-xs text-muted-foreground">
                {humanize(item.status)} · {formatTime(item.created_at)}
                {item.error ? ` · ${item.error}` : ""}
              </p>
            ))}
        </div>
      )}
    </section>
  )
}

export function IncidentDocuments({
  incidentId,
  canEdit,
}: {
  incidentId: string
  canEdit: boolean
}) {
  const documents = useQuery({
    queryKey: ["incidents", "documents", incidentId, "current"],
    queryFn: () => workspaceApi.documents(incidentId),
    refetchInterval: 5000,
    retry: false,
  })
  if (documents.isPending)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading incident documents…
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
  if (!documents.data) return null
  return (
    <>
      {documents.error && (
        <p role="alert" className="text-sm text-warning-foreground">
          {documents.error.message}
        </p>
      )}
      {(["postmortem", "status_page_draft"] as const).map((kind) => (
        <DocumentEditor
          key={kind}
          incidentId={incidentId}
          kind={kind}
          current={documents.data[kind]}
          canEdit={canEdit}
          operations={documents.data.operations}
        />
      ))}
    </>
  )
}
