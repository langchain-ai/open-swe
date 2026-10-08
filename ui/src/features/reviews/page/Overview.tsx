import { useQuery } from "@tanstack/react-query"
import { useCallback, useLayoutEffect, useRef, useState } from "react"
import type { ReactNode } from "react"
import { CaretDownIcon } from "@phosphor-icons/react"

import { reviewImageProxyUrl } from "@/lib/api"
import { formatRelativeTime } from "@/lib/utils"
import { cn } from "@/lib/utils"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { Skeleton } from "@/components/ui/skeleton"
import { HumanInputText } from "@/features/reviews/components/HumanInputCard"
import { PullRequestLabels } from "@/features/reviews/components/PullRequestLabels"
import { AgentMark } from "./AgentMark"
import { reviewQueries, type PullRequestRef } from "./queries"
import { StandingPanel } from "./Standing"

const CLAMP_PX = 320

/** The top of the centre scroll: where the PR stands, then what its author wrote. */
export function Overview({ pr }: { pr: PullRequestRef }) {
  const detail = useQuery(reviewQueries.detail(pr)).data
  if (!detail)
    return (
      <div
        aria-hidden
        className="flex w-full max-w-[920px] flex-col gap-4 px-4 pt-5 pb-8"
      >
        <Skeleton className="h-[236px] rounded-xl" />
        <Skeleton className="h-4 w-48" />
        <div className="flex flex-col gap-2">
          {[92, 100, 84, 96, 60].map((width) => (
            <Skeleton
              key={width}
              className="h-3.5"
              style={{ width: `${width}%` }}
            />
          ))}
        </div>
      </div>
    )
  return (
    <div className="flex w-full max-w-[920px] flex-col gap-4 px-4 pt-5 pb-8">
      <StandingPanel pr={pr} />
      {detail && detail.walkthrough?.human_input && (
        <section
          aria-label="Human input"
          className="rounded-xl border border-dashed border-border px-4 py-3"
        >
          <p className="mb-1 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <AgentMark />
            What people asked for
          </p>
          <HumanInputText summary={detail.walkthrough.human_input} />
        </section>
      )}
      {detail && (
        <Description
          pr={pr}
          body={detail.pr.body}
          author={detail.pr.author?.login ?? null}
          avatar={detail.pr.author?.avatar_url ?? null}
          createdAt={detail.pr.created_at}
          labels={
            <PullRequestLabels
              owner={pr.owner}
              repo={pr.repo}
              number={pr.number}
              labels={detail.pr.labels}
            />
          }
        />
      )}
    </div>
  )
}

function Description({
  pr,
  body,
  author,
  avatar,
  createdAt,
  labels,
}: {
  pr: PullRequestRef
  body: string
  author: string | null
  avatar: string | null
  createdAt: string | null
  labels: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const { owner, repo, number } = pr
  const proxyImage = useCallback(
    (src: string) => reviewImageProxyUrl(owner, repo, number, src),
    [owner, repo, number]
  )
  const [overflows, setOverflows] = useState(false)
  const [expanded, setExpanded] = useState(false)
  useLayoutEffect(() => {
    const node = ref.current
    if (!node) return
    const measure = () => setOverflows(node.scrollHeight > CLAMP_PX + 48)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [body])
  const clamped = overflows && !expanded
  return (
    <article aria-label="Description" className="group/description">
      <header className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
        {avatar && (
          <img
            src={avatar}
            alt=""
            className="size-5 rounded-full"
            loading="lazy"
          />
        )}
        <a
          href={`https://github.com/${author ?? ""}`}
          target="_blank"
          rel="noreferrer"
          className="font-medium text-foreground hover:underline"
        >
          {author ?? "Unknown"}
        </a>
        <a
          href={`https://github.com/${pr.owner}/${pr.repo}/pull/${pr.number}`}
          target="_blank"
          rel="noreferrer"
          className="hover:text-foreground hover:underline"
        >
          opened this{" "}
          {createdAt ? formatRelativeTime(new Date(createdAt).getTime()) : ""}
        </a>
        <span className="ml-auto">{labels}</span>
      </header>
      <div
        ref={ref}
        className={cn(
          "relative text-[13.5px] leading-[1.65]",
          clamped && "overflow-hidden"
        )}
        style={clamped ? { maxHeight: CLAMP_PX } : undefined}
      >
        {body.trim() ? (
          <Markdown
            content={body}
            transformImageUrl={proxyImage}
            enlargeImages
          />
        ) : (
          <p className="text-muted-foreground italic">No description.</p>
        )}
        {clamped && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-20 bg-gradient-to-t from-background to-transparent" />
        )}
      </div>
      {overflows && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
        >
          <CaretDownIcon
            className={cn(
              "size-3 transition-transform",
              expanded && "rotate-180"
            )}
          />
          {expanded ? "Show less" : "Show the whole description"}
        </button>
      )}
    </article>
  )
}
