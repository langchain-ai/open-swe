import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { Textarea } from "@langchain/macaw-components/Textarea"

import type { AnnouncementSummary } from "@/lib/api"
import { SettingsSection } from "@/components/AppShell"
import { api } from "@/lib/api"
import { invalidationTopic } from "@/lib/invalidations/topics"

import { ConfirmDialog } from "./ConfirmDialog"

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
      setMessage(`Sending to ${pluralize(result.recipients)}.`)
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
      description="DM a message from the Open SWE bot to everyone with a connected Slack account, such as news of a feature that just shipped. Admins can also ask Open SWE to do this from a Slack DM."
    >
      <div className="flex flex-col gap-space-2 p-space-4">
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
        <div className="flex items-center justify-between gap-space-2">
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
        description="Everyone with a connected Slack account gets this message from the Open SWE bot. A sent announcement cannot be recalled."
        confirmLabel="Send announcement"
        pendingLabel="Starting…"
        pending={send.isPending}
        onConfirm={() => send.mutate(false)}
      >
        <p className="max-h-48 overflow-y-auto rounded-md border border-default p-space-2 text-xs whitespace-pre-wrap text-primary">
          {draft}
        </p>
      </ConfirmDialog>
    </SettingsSection>
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
  if (loading) return <Skeleton className="h-16" />
  if (failed) {
    return (
      <div role="alert">
        <Banner intent="error" title="Could not load past announcements." />
      </div>
    )
  }
  if (!items.length) return null
  return (
    <div className="flex flex-col gap-0.5 pt-space-2">
      <span className="text-xs font-medium text-primary">Sent</span>
      {items.map((item) => (
        <div
          key={item.id}
          className="flex items-start justify-between gap-space-2 border-b border-default py-space-1 text-xs last:border-b-0"
        >
          <div className="flex min-w-0 flex-1 flex-col">
            <span className="truncate text-primary">{item.text}</span>
            <span className="text-secondary">
              {item.author || "Unknown admin"} ·{" "}
              {new Date(item.created_at).toLocaleString()}
            </span>
          </div>
          <span className="shrink-0 text-secondary">{progress(item)}</span>
        </div>
      ))}
    </div>
  )
}

function progress(item: AnnouncementSummary): string {
  const failed = item.failed ? `, ${item.failed} failed` : ""
  if (item.finished_at) return `${item.sent} delivered${failed}`
  return `Sending ${item.sent + item.failed}/${item.recipients}${failed}`
}

function pluralize(count: number): string {
  return `${count} ${count === 1 ? "person" : "people"}`
}
