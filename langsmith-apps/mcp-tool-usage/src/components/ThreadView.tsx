import { Badge } from "@langchain/macaw-components/Badge"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { CodeLite } from "@langchain/macaw-components/Code/CodeLite"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { Text } from "@langchain/macaw-components/Text"
import { cn } from "@langchain/macaw-components/utils/cn"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { ShuffleIcon } from "@phosphor-icons/react/dist/ssr/Shuffle"
import { useEffect, useMemo, useState } from "react"
import {
  fetchThreadCandidates,
  fetchThreadUrl,
  fetchTrajectory,
  openRun,
} from "../api"
import { CopyLink } from "./CopyLink"
import { focusThread, messageText, toolCalls } from "../lib/messages"
import { formatCount, parseToolName } from "../lib/tools"
import type {
  ThreadCandidate,
  ToolCallBlock,
  ToolUsage,
  TrajectoryMessage,
  WindowKey,
} from "../types"

type ViewMode = "focused" | "full"

// Results carry the thread they belong to, so a stale response never renders under a newer selection.
interface LoadedTrajectory {
  threadId: string
  messages: TrajectoryMessage[] | null
  failed: boolean
}

interface LoadedUrl {
  threadId: string
  url: string
}

const TEXT_LIMIT = 1200
const RESULT_LIMIT = 4000

export function ThreadView({
  projectId,
  tool,
  window,
}: {
  projectId: string
  tool: ToolUsage
  window: WindowKey
}) {
  const [candidates, setCandidates] = useState<ThreadCandidate[] | null>(null)
  const [candidateIndex, setCandidateIndex] = useState(0)
  const [candidatesFailed, setCandidatesFailed] = useState(false)
  const [trajectory, setTrajectory] = useState<LoadedTrajectory | null>(null)
  const [recordedUrl, setRecordedUrl] = useState<LoadedUrl | null>(null)
  const [mode, setMode] = useState<ViewMode>("focused")
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [openFailed, setOpenFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetchThreadCandidates(projectId, tool.runName, window)
      .then((result) => !cancelled && setCandidates(result))
      .catch((e) => {
        console.error("Failed to load tool calls", e)
        if (!cancelled) setCandidatesFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [projectId, tool.runName, window])

  const candidate = candidates?.[candidateIndex] ?? null

  useEffect(() => {
    if (!candidate) return
    let cancelled = false
    const { threadId } = candidate
    fetchThreadUrl(projectId, candidate.calls[0])
      .then((url) => !cancelled && setRecordedUrl({ threadId, url }))
      .catch((e) => console.error("Failed to load thread URL", e))
    fetchTrajectory(projectId, threadId)
      .then(
        (messages) =>
          !cancelled && setTrajectory({ threadId, messages, failed: false })
      )
      .catch((e) => {
        console.error("Failed to load thread", e)
        if (!cancelled)
          setTrajectory({ threadId, messages: null, failed: true })
      })
    return () => {
      cancelled = true
    }
  }, [projectId, candidate])

  const current =
    trajectory && trajectory.threadId === candidate?.threadId
      ? trajectory
      : null
  const messages = current?.messages ?? null
  const messagesFailed = current?.failed ?? false
  const threadUrl =
    recordedUrl && recordedUrl.threadId === candidate?.threadId
      ? recordedUrl.url
      : null

  const thread = useMemo(
    () =>
      messages
        ? focusThread(messages, tool.runName, mode === "focused", expanded)
        : null,
    [messages, tool.runName, mode, expanded]
  )

  return (
    <Card className="gap-space-4 flex min-w-0 flex-col">
      <div className="gap-space-3 flex flex-wrap items-start justify-between">
        <div className="gap-space-1 flex min-w-0 flex-col">
          <div className="gap-space-2 flex flex-wrap items-center">
            <Badge color="secondary" size="sm">
              {tool.connection}
            </Badge>
            <Text variant="h3" className="break-all">
              {tool.tool}
            </Text>
          </div>
          <Text variant="xs" color="tertiary">
            {candidate
              ? `Representative thread ${candidateIndex + 1} of ${candidates?.length} · ${
                  candidate.calls.length
                } call${candidate.calls.length === 1 ? "" : "s"} here · ${formatCount(tool.count)} in window`
              : `${formatCount(tool.count)} calls in window`}
          </Text>
          {threadUrl && <CopyLink key={threadUrl} url={threadUrl} />}
        </div>
        <div className="gap-space-2 flex flex-wrap items-center">
          <Button
            size="sm"
            variant="outlined"
            color="secondary"
            leftDecorator={ShuffleIcon}
            disabled={!candidates || candidates.length < 2}
            onClick={() => {
              setCandidateIndex(
                (index) => (index + 1) % (candidates?.length ?? 1)
              )
              setExpanded(new Set())
              setOpenFailed(false)
            }}
          >
            Another example
          </Button>
          <Button
            size="sm"
            variant="outlined"
            color="secondary"
            leftDecorator={ArrowSquareOutIcon}
            disabled={!candidate}
            onClick={() => {
              if (!candidate) return
              setOpenFailed(false)
              openRun(projectId, candidate.calls[0]).catch((e) => {
                console.error("Failed to resolve run URL", e)
                setOpenFailed(true)
              })
            }}
          >
            Open in LangSmith
          </Button>
        </div>
      </div>

      {openFailed && (
        <Banner intent="error" title="Couldn't open this run in LangSmith" />
      )}

      <GroupedTabs<ViewMode>
        size="sm"
        className="self-start"
        value={mode}
        onChange={setMode}
        options={[
          { value: "focused", display: "Tool calls" },
          { value: "full", display: "Full thread" },
        ]}
      />

      {renderBody()}
    </Card>
  )

  function renderBody() {
    if (candidatesFailed) {
      return <Banner intent="error" title="Couldn't load calls for this tool" />
    }
    if (!candidates) return <Loading label="Finding a representative thread…" />
    if (candidates.length === 0) {
      return (
        <EmptyState title="No calls with a thread in this window" size="sm" />
      )
    }
    if (messagesFailed)
      return <Banner intent="error" title="Couldn't load this thread" />
    if (!thread) return <Loading label="Loading thread…" />

    return (
      <ol className="gap-space-3 flex flex-col">
        {thread.rows.map((row) =>
          row.kind === "gap" ? (
            <li key={`gap-${row.indices[0]}`} className="flex justify-center">
              <Button
                size="xs"
                variant="plain"
                color="secondary"
                onClick={() =>
                  setExpanded((prev) => new Set([...prev, ...row.indices]))
                }
              >
                {`Show ${row.indices.length} hidden message${row.indices.length === 1 ? "" : "s"}`}
              </Button>
            </li>
          ) : (
            <li key={row.index}>
              <MessageRow
                message={row.message}
                runName={tool.runName}
                matchIds={thread.matchIds}
                toolNames={thread.toolNames}
              />
            </li>
          )
        )}
      </ol>
    )
  }
}

function Loading({ label }: { label: string }) {
  return (
    <div
      role="status"
      className="gap-space-2 py-space-8 flex items-center justify-center"
    >
      <Spinner size="sm" />
      <Text variant="sm" color="tertiary">
        {label}
      </Text>
    </div>
  )
}

function displayToolName(name: string): string {
  if (!name.startsWith("mcp_")) return name
  const { connection, tool } = parseToolName(name)
  return `${connection} · ${tool}`
}

function MessageRow({
  message,
  runName,
  matchIds,
  toolNames,
}: {
  message: TrajectoryMessage
  runName: string
  matchIds: Set<string>
  toolNames: Map<string, string>
}) {
  if (message.role === "tool") {
    const matched =
      message.tool_call_id != null && matchIds.has(message.tool_call_id)
    const name =
      (message.tool_call_id && toolNames.get(message.tool_call_id)) || "tool"
    return (
      <Bubble label={`Result · ${displayToolName(name)}`} highlight={matched}>
        <ToolPayload
          value={messageText(message)}
          limit={matched ? RESULT_LIMIT : TEXT_LIMIT}
        />
      </Bubble>
    )
  }

  const text = messageText(message)
  const calls = toolCalls(message)
  return (
    <Bubble
      label={message.role === "human" ? "User" : "Agent"}
      tone={message.role === "human" ? "human" : "ai"}
    >
      {text && <ClampedText value={text} limit={TEXT_LIMIT} />}
      {calls.length > 0 && (
        <div className="gap-space-2 flex flex-col">
          {calls.some((call) => call.name !== runName) && (
            <div className="gap-space-1 flex flex-wrap">
              {calls
                .filter((call) => call.name !== runName)
                .map((call) => (
                  <Badge key={call.id} color="plain" size="xs">
                    {displayToolName(call.name)}
                  </Badge>
                ))}
            </div>
          )}
          {calls
            .filter((call) => call.name === runName)
            .map((call) => (
              <MatchedCall key={call.id} call={call} />
            ))}
        </div>
      )}
      {!text && calls.length === 0 && (
        <Text variant="xs" color="tertiary">
          (no text)
        </Text>
      )}
    </Bubble>
  )
}

function MatchedCall({ call }: { call: ToolCallBlock }) {
  return (
    <div className="gap-space-1 border-brand bg-surface-level-1 p-space-2 flex flex-col rounded-lg border">
      <Text variant="xs" weight="medium">
        {`Call · ${displayToolName(call.name)}`}
      </Text>
      <CodeLite
        language="json"
        value={JSON.stringify(call.args, null, 2)}
        wrapLines
      />
    </div>
  )
}

function Bubble({
  label,
  tone,
  highlight,
  children,
}: {
  label: string
  tone?: "human" | "ai"
  highlight?: boolean
  children: React.ReactNode
}) {
  return (
    <div
      className={cn(
        "gap-space-2 p-space-3 flex min-w-0 flex-col rounded-lg border",
        highlight ? "border-brand" : "border-secondary",
        tone === "human" ? "bg-surface-level-3" : "bg-surface-level-2"
      )}
    >
      <Text variant="xs" weight="medium" color="tertiary">
        {label}
      </Text>
      {children}
    </div>
  )
}

function ClampedText({ value, limit }: { value: string; limit: number }) {
  const [open, setOpen] = useState(false)
  const clipped = !open && value.length > limit
  return (
    <div className="gap-space-1 flex w-full min-w-0 flex-col items-start">
      <p className="w-full text-sm [overflow-wrap:anywhere] whitespace-pre-wrap text-primary">
        {clipped ? `${value.slice(0, limit)}…` : value}
      </p>
      {value.length > limit && (
        <Button
          size="xs"
          variant="underlined"
          color="secondary"
          onClick={() => setOpen(!open)}
        >
          {open ? "Show less" : "Show all"}
        </Button>
      )}
    </div>
  )
}

function ToolPayload({ value, limit }: { value: string; limit: number }) {
  const pretty = useMemo(() => {
    try {
      return JSON.stringify(JSON.parse(value), null, 2)
    } catch {
      return null
    }
  }, [value])
  if (pretty != null && pretty.length <= limit) {
    return <CodeLite language="json" value={pretty} wrapLines />
  }
  return <ClampedText value={value} limit={limit} />
}
