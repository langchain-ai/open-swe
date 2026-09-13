import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { Copy } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { citationMarkdown } from "./citations"
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
  DocumentOperation,
  DocumentRevision,
} from "./workspace-api"

function DocumentEditor({
  incidentId,
  current,
  canEdit,
  operations,
}: {
  incidentId: string
  current: DocumentRevision | null
  canEdit: boolean
  operations: DocumentOperation[]
}) {
  const client = useQueryClient()
  const kind = "postmortem"
  const title = "Postmortem"
  const [draft, setDraft] = useState<{ markdown: string; base: number } | null>(
    null
  )
  const [showHistory, setShowHistory] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
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
      await navigator.clipboard.writeText(
        citationMarkdown(value, current?.evidence ?? [])
      )
      setCopyNotice("Incident copied as Markdown.")
    } catch {
      setCopyNotice(
        "Unable to copy. Select the incident text to copy it manually."
      )
    }
  }
  return (
    <section className="space-y-4 rounded-xl border border-border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium">{title}</h2>
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs text-muted-foreground">
            {current
              ? `Revision ${current.revision} · ${current.author} · ${formatTime(current.created_at)}`
              : "No saved revision"}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={!value.trim()}
            onClick={() => void copy()}
          >
            <Copy className="size-3.5" />
            Copy incident
          </Button>
        </div>
      </div>
      {canEdit && (
        <div className="flex items-center gap-2">
          <div role="group" aria-label={`${title} view`} className="flex gap-1">
            <Button
              size="sm"
              variant={isEditing ? "ghost" : "secondary"}
              aria-label={`Preview ${title.toLowerCase()}`}
              aria-pressed={!isEditing}
              onClick={() => setIsEditing(false)}
            >
              Preview
            </Button>
            <Button
              size="sm"
              variant={isEditing ? "secondary" : "ghost"}
              aria-label={`Edit ${title.toLowerCase()}`}
              aria-pressed={isEditing}
              onClick={() => setIsEditing(true)}
            >
              Edit
            </Button>
          </div>
          {draft && (
            <span className="text-xs text-muted-foreground">
              Unsaved changes
            </span>
          )}
        </div>
      )}
      {canEdit && isEditing ? (
        <Textarea
          aria-label={title}
          value={value}
          maxLength={200000}
          disabled={pending}
          className="min-h-64 font-mono"
          placeholder="Summary, impact, timeline, cause, mitigation, resolution, follow-ups, and evidence…"
          onChange={(event) =>
            setDraft({
              markdown: event.target.value,
              base: draft?.base ?? current?.revision ?? 0,
            })
          }
        />
      ) : value ? (
        <div className="mx-auto max-w-3xl py-5 sm:px-4">
          <Markdown
            content={citationMarkdown(value, current?.evidence ?? [])}
          />
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">No document yet.</p>
      )}
      {current?.evidence?.length ? (
        <ul className="space-y-2 text-xs">
          {current.evidence.map((evidence, index) => (
            <li key={evidence.id}>
              {evidence.available ? (
                <ExternalLink href={evidence.url}>
                  [{index + 1}] {humanize(evidence.source)} source
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
            <div className="mt-3">
              <Markdown
                content={citationMarkdown(
                  current.markdown,
                  current.evidence ?? []
                )}
              />
            </div>
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
            Save postmortem
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
                <div className="mt-3">
                  <Markdown
                    content={citationMarkdown(
                      revision.markdown,
                      revision.evidence ?? []
                    )}
                  />
                </div>
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
      <DocumentEditor
        incidentId={incidentId}
        current={documents.data.postmortem}
        canEdit={canEdit}
        operations={documents.data.operations}
      />
    </>
  )
}
