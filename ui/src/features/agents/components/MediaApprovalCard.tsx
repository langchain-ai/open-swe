import { useMemo, useState } from "react"
import {
  ExternalLink,
  ImageIcon,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react"

import type { MediaRequest } from "@/features/agents/lib/types"
import { agentsApi } from "@/features/agents/lib/api"
import {
  useMediaRequestDecision,
  useMediaRequests,
} from "@/features/agents/lib/queries"
import { Button } from "@/components/ui/button"

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatExpiry(epoch: number | null): string {
  if (!epoch) return "unknown"
  const ms = epoch * 1000 - Date.now()
  if (ms <= 0) return "expired"
  const minutes = Math.ceil(ms / 60_000)
  return minutes >= 60
    ? `${Math.floor(minutes / 60)}h ${minutes % 60}m`
    : `${minutes}m`
}

export function MediaApprovalCard({
  threadId,
  pollWhileActive = false,
}: {
  threadId: string
  pollWhileActive?: boolean
}) {
  const query = useMediaRequests(threadId, { pollWhileActive })
  const decision = useMediaRequestDecision(threadId)
  const [error, setError] = useState<string | null>(null)
  const requests = useMemo(
    () =>
      (query.data?.requests ?? []).filter(
        (request) => request.status === "pending"
      ),
    [query.data?.requests]
  )

  if (requests.length === 0) return null

  const decide = async (request: MediaRequest, kind: "approve" | "reject") => {
    setError(null)
    try {
      await decision.mutateAsync({
        fingerprint: request.fingerprint,
        decision: kind,
      })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div
      data-testid="media-approval-group"
      className="mt-4 flex w-full flex-col gap-3"
    >
      {requests.map((request) => {
        const busy = decision.isPending
        const isImage = request.contentType.startsWith("image/")
        const prUrl = `https://github.com/${request.owner}/${request.repo}/pull/${request.pullNumber}`
        return (
          <section
            key={request.fingerprint}
            data-testid="media-approval-card"
            className="rounded-xl border border-border bg-card p-4 shadow-sm"
          >
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 size-5 shrink-0 text-primary" />
              <div className="min-w-0">
                <p className="text-[0.68rem] font-semibold tracking-wider text-primary uppercase">
                  Media upload paused for review
                </p>
                <h2 className="mt-1 text-base font-semibold text-foreground">
                  Approve attaching {request.fileName} to a pull request
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  Open SWE wants to upload this {request.contentType} (
                  {formatSize(request.sizeBytes)}) to{" "}
                  <a
                    href={prUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-0.5 text-primary underline-offset-2 hover:underline"
                  >
                    {request.owner}/{request.repo}#{request.pullNumber}
                    <ExternalLink className="size-3" />
                  </a>
                  {request.pullTitle ? ` — ${request.pullTitle}` : ""}. The
                  upload posts as you and runs once, on approval only.
                </p>
              </div>
            </div>

            <div className="mt-3 overflow-hidden rounded-md border border-border bg-muted/40">
              {isImage ? (
                <img
                  data-testid="media-approval-preview"
                  src={agentsApi.mediaPreviewUrl(threadId, request.fingerprint)}
                  alt={request.fileName}
                  className="max-h-64 w-full object-contain"
                />
              ) : (
                <div className="flex items-center gap-2 p-3 text-xs text-muted-foreground">
                  <ImageIcon className="size-4 shrink-0" />
                  Video preview is not available; only the recorded bytes
                  (SHA-256{" "}
                  <code className="font-mono">
                    {request.digest.slice(0, 12)}…
                  </code>
                  ) will be uploaded.
                </div>
              )}
            </div>

            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-4">
              <div>
                <dt className="font-medium text-foreground">SHA-256</dt>
                <dd className="font-mono">{request.digest.slice(0, 16)}…</dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">Size</dt>
                <dd>{formatSize(request.sizeBytes)}</dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">Requested by</dt>
                <dd>{request.requestedBy ?? "agent"}</dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">Expires in</dt>
                <dd>{formatExpiry(request.expiresAtEpoch)}</dd>
              </div>
            </dl>

            <div className="mt-3 flex gap-3 border-l-2 border-warning-foreground bg-warning/10 p-3">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning-foreground" />
              <p className="text-xs text-muted-foreground">
                Approving uploads these exact bytes to GitHub under your account
                and posts them as a pull-request comment. The destination and
                file hash above are bound to this approval.
              </p>
            </div>

            {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

            <div className="mt-4 flex gap-2">
              <Button
                data-testid="media-approve"
                size="sm"
                disabled={busy}
                onClick={() => void decide(request, "approve")}
              >
                Approve and upload
              </Button>
              <Button
                data-testid="media-reject"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => void decide(request, "reject")}
              >
                Reject
              </Button>
            </div>
          </section>
        )
      })}
    </div>
  )
}
