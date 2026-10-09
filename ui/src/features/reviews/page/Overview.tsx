import { CaretDownIcon } from "@langchain/macaw-components/icons"
import { useQuery } from "@tanstack/react-query"
import { useCallback, useLayoutEffect, useRef, useState } from "react"
import type { ReactNode } from "react"

import { reviewImageProxyUrl, type ReviewUserRef } from "@/lib/api"
import { cn, formatRelativeTime } from "@/lib/utils"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { HumanInputText } from "@/features/reviews/components/HumanInputCard"
import { PullRequestLabels } from "@/features/reviews/components/PullRequestLabels"
import { githubUrls } from "@/features/reviews/lib/githubUrls"
import { ProfileLink } from "./notes/Byline"
import { AgentMark } from "./AgentMark"
import { reviewQueries, type PullRequestRef } from "./queries"
import { StandingPanel } from "./Standing"

const CLAMP_PX = 320

/** `#123` in prose links to that pull request's review page here; code is left alone. */
function linkPullRequestRefs(body: string, pr: PullRequestRef): string {
  return body
    .split(/(```[\s\S]*?```|`[^`\n]*`)/)
    .map((part, index) =>
      index % 2 === 1
        ? part
        : part.replace(
            /(^|[\s(])#(\d+)\b/g,
            (_match, lead: string, number: string) =>
              `${lead}[#${number}](/agents/reviews/${pr.owner}/${pr.repo}/${number})`
          )
    )
    .join("")
}

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
      {detail.walkthrough?.human_input && (
        <section
          aria-label="Human input"
          className="rounded-xl border border-dashed border-default px-4 py-3"
        >
          <p className="mb-1 flex items-center gap-1.5 text-xs font-medium text-secondary">
            <AgentMark />
            What people asked for
          </p>
          <HumanInputText summary={detail.walkthrough.human_input} />
        </section>
      )}
      <Description
        pr={pr}
        body={detail.pr.body}
        author={detail.pr.author}
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
    </div>
  )
}

function Description({
  pr,
  body,
  author,
  createdAt,
  labels,
}: {
  pr: PullRequestRef
  body: string
  author: ReviewUserRef | null
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
      <header className="mb-2 flex items-center gap-2 text-xs text-secondary">
        {author?.avatar_url && (
          <img
            src={author.avatar_url}
            alt=""
            className="size-5 rounded-full"
            loading="lazy"
          />
        )}
        <ProfileLink author={author} className="font-medium text-primary" />
        <a
          href={githubUrls.pullRequest(pr)}
          target="_blank"
          rel="noreferrer"
          className="hover:text-primary hover:underline"
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
            content={linkPullRequestRefs(body, pr)}
            transformImageUrl={proxyImage}
            enlargeImages
          />
        ) : (
          <p className="text-secondary italic">No description.</p>
        )}
        {clamped && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-20 bg-gradient-to-t from-background to-transparent" />
        )}
      </div>
      {overflows && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-secondary hover:text-primary"
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
