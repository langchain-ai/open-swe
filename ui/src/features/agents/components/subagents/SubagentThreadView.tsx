import { useMemo } from "react"
import type { ReactNode } from "react"
import { Link } from "@tanstack/react-router"
import { PageBand } from "@langchain/gtm-platform-design-system/patterns/page-band"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { cn } from "@langchain/gtm-platform-design-system/ui/cn"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"

import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle,
  ChevronRight,
} from "@/components/glyphs"
import { LoadError } from "@/components/LoadError"
import { Messages } from "@/features/agents/components/messages"
import { asString } from "@/features/agents/components/subagents/SubagentCard"
import { useThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"
import type { AgentThread, Message } from "@/features/agents/lib/types"

/**
 * A subagent viewed as a thread of its own: the task it was given, then
 * everything it said and did, read from the parent thread's transcript under
 * the subagent's namespace. Subagents cannot be replied to — they run to
 * completion on their own and report back to the parent — so there is no
 * composer.
 */
export function SubagentThreadView({
  thread,
  subagentId,
}: {
  thread: AgentThread
  subagentId: string
}) {
  const source = useThreadSource()
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const task =
    source.kind === "transcript" ? source.subagentTask(subagentId) : null
  const description = task ? asString(task.input.description) : ""
  const messages = useMemo(() => {
    if (!task || source.kind !== "transcript") return []
    const own = source.subagentMessages([...task.namespace, task.toolCallId])
    if (!description) return own
    // The transcript never records the task prompt as a human message — the
    // model wrote it, not a person — so the `task` call's input stands in.
    const prompt: Message = {
      id: `${task.toolCallId}:prompt`,
      author: "user",
      timestamp: task.startedAt,
      chunks: [{ kind: "text", text: description }],
    }
    return [prompt, ...own]
  }, [description, source, task])

  const title = description.split("\n", 1)[0]?.trim() || "Subagent"
  const subagentType = task ? asString(task.input.subagent_type) : ""
  const isRunning = task?.status === "in_progress"

  let body: ReactNode
  if (source.kind !== "transcript") {
    body = (
      <LoadError
        title="Subagent transcript unavailable"
        context={`Thread: ${thread.id}`}
        error="Only threads served by the transcript log keep their subagents' transcripts."
      />
    )
  } else if (source.isHydrating) {
    body = (
      <Stack
        aria-busy="true"
        aria-label="Loading subagent"
        gap="sm"
        className="mx-auto w-full max-w-thread px-4 pt-6"
      >
        <Skeleton className="h-3 w-5/6" />
        <Skeleton className="h-3 w-3/4" />
        <Skeleton className="h-3 w-1/2" />
      </Stack>
    )
  } else if (!task) {
    body = (
      <LoadError
        title="Subagent not found"
        context={`Thread: ${thread.id}`}
        error="This thread's transcript has no subagent with that id. It may belong to a turn older than the loaded window."
      />
    )
  } else {
    body = (
      <Messages
        messages={messages}
        threadId={thread.id}
        scrollKey={`${thread.id}:${subagentId}`}
        isStreaming={isRunning}
        contentWidthClass="max-w-thread"
        footer={
          !isRunning && (
            <Box
              render={<p />}
              className="px-1 pt-2 pb-6 text-center text-meta text-ink-subtle"
            >
              Subagents cannot be replied to. Follow up in the parent thread.
            </Box>
          )
        }
      />
    )
  }

  return (
    <Stack grow className="min-w-0">
      <Box
        render={<header />}
        data-desktop-drag-region=""
        className={cn(
          "shrink-0",
          // The macOS traffic lights overhang the 48px icon rail.
          isDesktop && "in-data-[rail-collapsed=true]:pl-6"
        )}
      >
        <PageBand variant="toolbar" edge="none">
          <Inline gap="xs" align="center" grow className="min-w-0">
            <Button
              variant="ghost"
              nativeButton={false}
              render={
                <Link
                  to="/agents/$threadId"
                  params={{ threadId: thread.id }}
                  search={{}}
                />
              }
              data-no-drag=""
              title={thread.title}
              className="min-w-0 shrink px-2 font-normal text-ink-muted"
            >
              <Icon icon={ArrowLeft} size="sm" />
              <Box render={<span />} className="max-w-48 truncate">
                {thread.title}
              </Box>
            </Button>
            <Icon icon={ChevronRight} size="sm" className="text-ink-subtle" />
            <Box
              render={<span />}
              title={description || title}
              className="min-w-0 truncate px-1 font-medium text-ink"
            >
              {title}
            </Box>
            {subagentType && (
              <Badge tier="quiet" tone="neutral">
                {subagentType}
              </Badge>
            )}
            {task?.status === "in_progress" ? (
              <Spinner
                size="sm"
                label="Subagent running"
                className="text-ink-subtle"
              />
            ) : task?.status === "error" ? (
              <Icon
                icon={AlertTriangle}
                size="sm"
                label="Subagent failed"
                className="text-attention"
              />
            ) : task ? (
              <Icon
                icon={CheckCircle}
                size="sm"
                label="Subagent finished"
                className="text-ink-subtle"
              />
            ) : null}
          </Inline>
        </PageBand>
      </Box>
      <Stack grow className="relative min-h-0 overflow-hidden">
        {body}
      </Stack>
    </Stack>
  )
}
