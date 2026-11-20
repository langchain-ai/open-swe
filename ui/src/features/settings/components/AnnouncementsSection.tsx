import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { LinearProgress } from "@langchain/macaw-components/LinearProgress"
import { SkeletonLines } from "@/components/Skeleton"
import { Textarea } from "@langchain/macaw-components/Textarea"

import type {
  AnnouncementApproval,
  AnnouncementsView,
  AnnouncementSummary,
} from "@/lib/api"
import { SlackMrkdwn } from "@/features/agents/components/messages/SlackMrkdwn"
import { SettingsSection } from "@/components/AppShell"
import { api } from "@/lib/api"
import { invalidationTopic } from "@/lib/invalidations/topics"

import { ConfirmDialog } from "@/components/ConfirmDialog"

const TEXT_MAX_CHARS = 3000
const QUERY_KEY = ["adminAnnouncements"]

export function AnnouncementsSection() {
  const qc = useQueryClient()
  const [text, setText] = useState("")
  const [confirming, setConfirming] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const announcements = useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.listAnnouncements,
    meta: { invalidatedBy: [invalidationTopic("announcements")] },
  })
  const recipients = announcements.data?.recipients ?? 0
  const draft = text.trim()

  const send = useMutation({
    mutationFn: (test: boolean) => api.sendAnnouncement(draft, test),
    meta: { silent: true },
    onSuccess: (result) => {
      setError(null)
      setConfirming(false)
      if (result.test) {
        setMessage("Sent the test to your Slack DMs.")
        return
      }
      setText("")
      setMessage(
        `Queued for ${pluralize(result.recipients)}. If your Slack account is connected, your DM is sent first.`
      )
      void qc.invalidateQueries({ queryKey: QUERY_KEY })
    },
    onError: (e: Error) => {
      setConfirming(false)
      setMessage(null)
      setError(e.message)
    },
  })

  return (
    <SettingsSection
      title="Announcements"
      description="DM a message from the Robin bot to everyone with a connected Slack account, such as news of a feature that just shipped. Admins can also ask Robin to do this from a Slack DM."
    >
      <div className="flex flex-col gap-space-2 p-space-4">
        <PendingApprovals items={announcements.data?.approvals ?? []} />
        <Textarea
          aria-label="Announcement"
          size="md"
          inputClassName="min-h-32"
          maxLength={TEXT_MAX_CHARS}
          placeholder="We just shipped… (Slack formatting: *bold*, _italic_, <https://example.com|links>)"
          value={text}
          onChange={(value) => {
            setText(value)
            setMessage(null)
            setError(null)
          }}
        />
        <div className="flex flex-wrap items-center justify-between gap-space-2">
          <span className="text-xs text-secondary">
            {announcements.isLoading
              ? "Counting recipients…"
              : `${pluralize(recipients)} with Slack connected`}
          </span>
          <div className="flex gap-space-2">
            <Button
              size="sm"
              color="secondary"
              variant="outlined"
              disabled={!draft || send.isPending}
              onClick={() => send.mutate(true)}
            >
              Send test to me
            </Button>
            <Button
              size="sm"
              color="primary"
              disabled={!draft || !recipients || send.isPending}
              onClick={() => setConfirming(true)}
            >
              Send to everyone
            </Button>
          </div>
        </div>
        {draft && (
          <div className="rounded-md bg-surface-level-2 p-space-3">
            <p className="mb-space-2 text-xs font-medium text-secondary">
              Preview
            </p>
            <div className="max-h-64 overflow-y-auto text-sm break-words whitespace-pre-wrap text-primary">
              <SlackMrkdwn text={draft} />
            </div>
          </div>
        )}
        {message && <p className="text-xs text-secondary">{message}</p>}
        {error && <p className="text-xs text-error-secondary">{error}</p>}
        <AnnouncementHistory
          loading={announcements.isLoading}
          failed={announcements.isError}
          items={announcements.data?.items ?? []}
        />
      </div>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={`DM ${pluralize(recipients)}?`}
        description="If your Slack account is connected, you receive the first DM, followed by everyone else. You can cancel remaining deliveries, but messages already sent cannot be recalled."
        confirmLabel="Send announcement"
        pendingLabel="Starting…"
        pending={send.isPending}
        onConfirm={() => send.mutate(false)}
      >
        <p className="max-h-48 overflow-y-auto rounded-md border border-default p-space-2 text-xs whitespace-pre-wrap text-primary">
          <SlackMrkdwn text={draft} />
        </p>
      </ConfirmDialog>
    </SettingsSection>
  )
}

function PendingApprovals({ items }: { items: Array<AnnouncementApproval> }) {
  const qc = useQueryClient()
  const [selected, setSelected] = useState<AnnouncementApproval | null>(null)
  const [error, setError] = useState<string | null>(null)
  const decide = useMutation({
    mutationFn: ({
      id,
      decision,
      payloadDigest,
    }: {
      id: string
      decision: "approve" | "reject"
      payloadDigest: string
    }) => api.decideAnnouncement(id, decision, payloadDigest),
    meta: { silent: true },
    onSuccess: (result) => {
      setSelected(null)
      setError(
        result.status === "approved" || result.status === "rejected"
          ? null
          : `Request is ${result.status}.`
      )
      void qc.invalidateQueries({ queryKey: QUERY_KEY })
    },
    onError: (e: Error) => {
      setSelected(null)
      setError(e.message)
      void qc.invalidateQueries({ queryKey: QUERY_KEY })
    },
  })
  if (!items.length && !error) return null
  return (
    <div
      id="announcement-approvals"
      className="flex flex-col gap-space-2 rounded-md bg-surface-level-2 p-space-3"
    >
      <span className="text-sm font-medium text-primary">
        Pending approvals
      </span>
      {items.map((item) => (
        <div key={item.id} className="flex flex-col gap-space-2">
          <span className="text-xs text-secondary">
            {pluralize(item.recipients)} with Slack connected at preparation ·
            Expires {new Date(item.expires_at).toLocaleString()}
          </span>
          <p className="max-h-48 overflow-y-auto text-xs whitespace-pre-wrap text-primary">
            {item.text}
          </p>
          <div className="flex gap-space-2">
            <Button
              size="sm"
              disabled={decide.isPending}
              onClick={() => setSelected(item)}
            >
              Review and send
            </Button>
            <Button
              size="sm"
              color="secondary"
              variant="outlined"
              disabled={decide.isPending}
              onClick={() =>
                decide.mutate({
                  id: item.id,
                  decision: "reject",
                  payloadDigest: item.payload_digest,
                })
              }
            >
              Decline
            </Button>
          </div>
        </div>
      ))}
      {error && (
        <p role="alert" className="text-xs text-error-secondary">
          {error}
        </p>
      )}
      <ConfirmDialog
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null)
        }}
        title={`DM ${pluralize(selected?.recipients ?? 0)}?`}
        description="This sends to the saved audience of Slack-linked Robin users. A sent announcement cannot be recalled."
        confirmLabel="Approve and send"
        pendingLabel="Starting…"
        pending={decide.isPending}
        onConfirm={() => {
          if (selected)
            decide.mutate({
              id: selected.id,
              decision: "approve",
              payloadDigest: selected.payload_digest,
            })
        }}
      >
        <p className="max-h-48 overflow-y-auto rounded-md border border-default p-space-2 text-xs whitespace-pre-wrap text-primary">
          {selected?.text}
        </p>
      </ConfirmDialog>
    </div>
  )
}

function AnnouncementHistory({
  loading,
  failed,
  items,
}: {
  loading: boolean
  failed: boolean
  items: Array<AnnouncementSummary>
}) {
  if (loading)
    return <SkeletonLines lines={3} label="Loading past announcements" />
  if (failed) {
    return (
      <div role="alert">
        <Banner intent="error" title="Could not load past announcements." />
      </div>
    )
  }
  if (!items.length) return null
  return (
    <div className="flex flex-col gap-space-3 pt-space-3">
      <h3 className="text-xs font-medium text-secondary">Delivery history</h3>
      {items.map((item) => (
        <AnnouncementCard key={item.id} item={item} />
      ))}
    </div>
  )
}

function AnnouncementCard({ item }: { item: AnnouncementSummary }) {
  const qc = useQueryClient()
  const cancel = useMutation({
    mutationFn: () => api.cancelAnnouncement(item.id),
    onMutate: async () => {
      await qc.cancelQueries({ queryKey: QUERY_KEY })
      const previous = qc.getQueryData<AnnouncementsView>(QUERY_KEY)
      qc.setQueryData<AnnouncementsView>(
        QUERY_KEY,
        (current) =>
          current && {
            ...current,
            items: current.items.map((entry) =>
              entry.id === item.id
                ? { ...entry, cancelled_at: new Date().toISOString() }
                : entry
            ),
          }
      )
      return { previous }
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) qc.setQueryData(QUERY_KEY, context.previous)
    },
    onSettled: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })
  const active = !item.finished_at && !item.cancelled_at
  const processed = item.sent + item.failed
  const status = cancel.isPending
    ? "Cancelling…"
    : item.cancelled_at
      ? "Cancelled"
      : item.finished_at
        ? "Completed"
        : "Sending"

  return (
    <article className="min-w-0 rounded-md bg-surface-level-2 p-space-3">
      <div className="mb-space-3 flex flex-wrap items-center justify-between gap-space-2">
        <div className="flex flex-wrap items-center gap-space-2 text-xs">
          <span className="font-medium text-primary" role="status">
            {status}
          </span>
          <span className="text-secondary">
            {item.sent} of {item.recipients} delivered
            {item.failed > 0 && ` · ${item.failed} failed`}
          </span>
        </div>
        {(active || cancel.isPending) && (
          <Button
            size="sm"
            color="secondary"
            variant="outlined"
            disabled={cancel.isPending}
            onClick={() => cancel.mutate()}
          >
            {cancel.isPending ? "Cancelling…" : "Cancel remaining"}
          </Button>
        )}
      </div>
      {active && (
        <LinearProgress
          className="mb-space-3"
          thicknessPx={3}
          aria-label="Announcement delivery progress"
          value={processed}
          max={Math.max(item.recipients, 1)}
        />
      )}
      <div className="max-h-64 overflow-y-auto text-sm break-words whitespace-pre-wrap text-primary">
        <SlackMrkdwn text={item.text} />
      </div>
      <p className="mt-space-3 text-xs text-secondary">
        {item.author || "Unknown admin"} ·{" "}
        {new Date(item.created_at).toLocaleString()}
      </p>
      {(active || item.cancelled_at) && (
        <p className="mt-space-1 text-xs text-secondary">
          {item.cancelled_at
            ? "Remaining deliveries stopped. A DM already in flight may still arrive."
            : "Cancel stops remaining deliveries. Messages already sent cannot be recalled."}
        </p>
      )}
      {cancel.isError && (
        <p role="alert" className="mt-space-2 text-xs text-error-secondary">
          {cancel.error.message}
        </p>
      )}
    </article>
  )
}

function pluralize(count: number): string {
  return `${count} ${count === 1 ? "person" : "people"}`
}
