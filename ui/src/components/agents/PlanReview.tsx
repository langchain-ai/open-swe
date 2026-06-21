import "@blocknote/core/fonts/inter.css"
import "@blocknote/mantine/style.css"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import * as Y from "yjs"
import { WebsocketProvider } from "y-websocket"
import {
  CommentsExtension,
  DefaultThreadStoreAuth,
  YjsThreadStore,
} from "@blocknote/core/comments"
import { useCreateBlockNote } from "@blocknote/react"
import { BlockNoteView } from "@blocknote/mantine"

import type { HarvestedComment, PlanData } from "@/lib/plan"
import { approvePlan, planCollabUrl, rejectPlan } from "@/lib/plan"
import { Button } from "@/components/ui/button"
import { useResolvedTheme } from "@/lib/theme"

const CURSOR_COLORS = [
  "#e11d48",
  "#2563eb",
  "#059669",
  "#d97706",
  "#7c3aed",
  "#0891b2",
]

function colorFor(id: string): string {
  let hash = 0
  for (let i = 0; i < id.length; i++)
    hash = (hash * 31 + id.charCodeAt(i)) >>> 0
  return CURSOR_COLORS[hash % CURSOR_COLORS.length] ?? "#2563eb"
}

function bodyToText(body: unknown): string {
  if (typeof body === "string") return body
  if (!Array.isArray(body)) return ""
  const parts: Array<string> = []
  for (const block of body) {
    const content = (block as { content?: unknown }).content
    if (typeof content === "string") parts.push(content)
    else if (Array.isArray(content))
      parts.push(
        content.map((node) => (node as { text?: string }).text ?? "").join("")
      )
  }
  return parts.join("\n").trim()
}

export function PlanReview({ plan }: { plan: PlanData }) {
  const resolvedTheme = useResolvedTheme()

  const { doc, provider, threadStore } = useMemo(() => {
    const ydoc = new Y.Doc()
    const wsProvider = new WebsocketProvider(
      planCollabUrl(),
      plan.threadId,
      ydoc,
      { connect: true }
    )
    wsProvider.awareness.setLocalStateField("user", {
      name: plan.user.name,
      color: colorFor(plan.user.id),
    })
    const auth = new DefaultThreadStoreAuth(
      plan.user.id,
      plan.isOwner ? "editor" : "comment"
    )
    const store = new YjsThreadStore(plan.user.id, ydoc.getMap("threads"), auth)
    return { doc: ydoc, provider: wsProvider, threadStore: store }
  }, [plan.threadId, plan.user.id, plan.user.name, plan.isOwner])

  useEffect(() => {
    return () => {
      provider.destroy()
      doc.destroy()
    }
  }, [provider, doc])

  // Track reviewers' display names from awareness so harvested comments are
  // attributed to people, not raw ids.
  const usersRef = useRef<Record<string, string>>({
    [plan.user.id]: plan.user.name,
  })
  useEffect(() => {
    const sync = () => {
      for (const state of provider.awareness.getStates().values()) {
        const user = (state as { user?: { name?: string } }).user
        if (user?.name) usersRef.current[user.name] = user.name
      }
    }
    provider.awareness.on("change", sync)
    sync()
    return () => provider.awareness.off("change", sync)
  }, [provider])

  const resolveUsers = useCallback(
    (userIds: Array<string>) =>
      Promise.resolve(
        userIds.map((id) => ({
          id,
          username: usersRef.current[id] ?? id,
          avatarUrl: "",
        }))
      ),
    []
  )

  const editor = useCreateBlockNote(
    {
      collaboration: {
        provider,
        fragment: doc.getXmlFragment("blocknote"),
        user: { name: plan.user.name, color: colorFor(plan.user.id) },
        showCursorLabels: "activity",
      },
      extensions: [CommentsExtension({ threadStore, resolveUsers })],
    },
    [provider, threadStore, resolveUsers]
  )

  // Seed the shared document from the agent's plan markdown the first time the
  // owner opens an empty plan. The `seeded` flag + sync barrier prevent a second
  // reviewer from double-seeding.
  useEffect(() => {
    if (!plan.isOwner) return
    let cancelled = false
    const seed = async (isSynced: boolean) => {
      if (!isSynced || cancelled) return
      const meta = doc.getMap<boolean>("meta")
      if (meta.get("seeded")) return
      const blocks = editor.document
      const empty =
        blocks.length === 0 ||
        (blocks.length === 1 && bodyToText(blocks) === "")
      if (!empty || !plan.markdown.trim()) return
      const parsed = await editor.tryParseMarkdownToBlocks(plan.markdown)
      // Re-check after the await: another reviewer may have seeded concurrently.
      // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition
      if (cancelled || !parsed.length || meta.get("seeded")) return
      editor.replaceBlocks(editor.document, parsed)
      meta.set("seeded", true)
    }
    provider.on("sync", seed)
    if (provider.synced) void seed(true)
    return () => {
      cancelled = true
      provider.off("sync", seed)
    }
  }, [editor, provider, doc, plan.markdown, plan.isOwner])

  const harvest = useCallback((): Array<HarvestedComment> => {
    const out: Array<HarvestedComment> = []
    for (const thread of threadStore.getThreads().values()) {
      for (const comment of thread.comments) {
        if (!comment.body) continue
        out.push({
          author: usersRef.current[comment.userId] ?? comment.userId,
          body: bodyToText(comment.body),
          resolved: thread.resolved,
        })
      }
    }
    return out
  }, [threadStore])

  const [decision, setDecision] = useState<string | null>(null)
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null)
  const decide = useCallback(
    async (kind: "approve" | "reject") => {
      setBusy(kind)
      try {
        const comments = harvest()
        if (kind === "approve") await approvePlan(plan.threadId, comments)
        else await rejectPlan(plan.threadId, comments)
        setDecision(
          kind === "approve"
            ? "Plan approved — the agent is implementing it."
            : "Changes requested — the agent is revising the plan."
        )
      } finally {
        setBusy(null)
      }
    },
    [harvest, plan.threadId]
  )

  return (
    <div
      data-testid="plan-review"
      className="flex min-h-0 flex-1 flex-col bg-[var(--ui-bg)] text-[var(--ui-text)]"
    >
      <div className="flex items-center justify-between gap-4 border-b border-[var(--ui-border)] px-6 py-3">
        <div>
          <h1 className="text-base font-semibold text-[var(--ui-text)]">
            Implementation plan
          </h1>
          <p className="text-xs text-[var(--ui-text-dim)]">
            Reviewing as {plan.user.name}
            {plan.isOwner ? " (owner)" : ""} · status:{" "}
            <span data-testid="plan-status">{plan.status}</span>
            {" · select text in the plan to comment"}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {decision && (
            <span
              data-testid="plan-decision"
              className="text-xs text-[var(--ui-text-dim)]"
            >
              {decision}
            </span>
          )}
          {plan.isOwner && (
            <Button
              data-testid="approve-plan"
              disabled={busy !== null || decision !== null}
              onClick={() => void decide("approve")}
            >
              Approve
            </Button>
          )}
          <Button
            data-testid="reject-plan"
            variant="secondary"
            disabled={busy !== null || decision !== null}
            onClick={() => void decide("reject")}
          >
            Request changes
          </Button>
        </div>
      </div>
      <div
        className="min-h-0 flex-1 overflow-auto py-4"
        data-testid="plan-document"
        data-color-scheme={resolvedTheme}
      >
        <BlockNoteView
          editor={editor}
          editable={plan.isOwner && !decision}
          theme={resolvedTheme}
        />
      </div>
    </div>
  )
}
