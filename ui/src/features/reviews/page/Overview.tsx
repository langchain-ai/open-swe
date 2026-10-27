import { CaretDownIcon } from "@langchain/macaw-components/icons"
import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useLayoutEffect, useRef, useState } from "react"
import type { ReactNode } from "react"

import { api, reviewImageProxyUrl, type ReviewUserRef } from "@/lib/api"
import { cn } from "@/lib/utils"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { HumanInputText } from "@/features/reviews/components/HumanInputCard"
import { PullRequestLabels } from "@/features/reviews/components/PullRequestLabels"
import { githubUrls } from "@/features/reviews/lib/githubUrls"
import { ProfileLink } from "./notes/Byline"
import { AgentMark } from "./AgentMark"
import { reviewQueries, type PullRequestRef } from "./queries"
import { StandingPanel } from "./Standing"
import { RelativeTimestamp } from "./RelativeTimestamp"

const CLAMP_PX = 320

/** `#123` and `owner/repo#123` in prose link to that pull request's review page here; code is left alone. */
function linkPullRequestRefs(body: string, pr: PullRequestRef): string {
  return body
    .split(/(```[\s\S]*?```|`[^`\n]*`)/)
    .map((part, index) =>
      index % 2 === 1
        ? part
        : part.replace(
            /(^|[\s(])(?:([A-Za-z0-9-]+)\/([\w.-]+))?#(\d+)\b/g,
            (
              _match,
              lead: string,
              owner: string | undefined,
              repo: string | undefined,
              number: string
            ) => {
              const target = owner && repo ? { owner, repo } : pr
              const text = owner ? `${owner}/${repo}#${number}` : `#${number}`
              return `${lead}[${text}](/agents/reviews/${encodeURIComponent(target.owner)}/${encodeURIComponent(target.repo)}/${number})`
            }
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
        className="flex w-full flex-col gap-space-4 px-space-4 pt-space-4 pb-space-6"
      >
        <Skeleton className="h-[236px] rounded-xl" />
        <Skeleton className="h-4 w-48" />
        <div className="flex flex-col gap-space-2">
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
    <div className="flex w-full flex-col gap-space-4 px-space-4 pt-space-4 pb-space-6">
      <StandingPanel pr={pr} />
      {detail.walkthrough?.human_input && (
        <details
          aria-label="Requested behavior"
          className="group/requested rounded-xl border border-dashed border-default px-space-4 py-space-3"
        >
          <summary className="flex cursor-pointer list-none items-center gap-space-2 text-xs font-medium text-secondary hover:text-primary [&::-webkit-details-marker]:hidden">
            <AgentMark />
            Requested behavior
            <CaretDownIcon
              aria-hidden
              className="ml-auto size-3 -rotate-90 group-open/requested:rotate-0"
            />
          </summary>
          <div className="mt-space-1">
            <HumanInputText summary={detail.walkthrough.human_input} />
          </div>
        </details>
      )}
      <Description
        key={`${pr.owner}/${pr.repo}/${pr.number}`}
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
  const editButton = useRef<HTMLButtonElement>(null)
  const queryClient = useQueryClient()
  const detailQuery = reviewQueries.detail(pr)
  const [draft, setDraft] = useState<string | null>(null)
  const closeEditor = () => {
    setDraft(null)
    requestAnimationFrame(() =>
      editButton.current?.focus({ preventScroll: true })
    )
  }
  const update = useMutation({
    mutationFn: (text: string) =>
      api.updatePullRequestDescription(pr.owner, pr.repo, pr.number, text),
    onMutate: async (text) => {
      await queryClient.cancelQueries(detailQuery)
      const previous = queryClient.getQueryData(detailQuery.queryKey)
      queryClient.setQueryData(detailQuery.queryKey, (detail) =>
        detail ? { ...detail, pr: { ...detail.pr, body: text } } : detail
      )
      closeEditor()
      return previous
    },
    meta: { errorTitle: "Couldn't update description" },
    onError: (_error, text, previous) => {
      queryClient.setQueryData(detailQuery.queryKey, previous)
      setDraft(text)
    },
    onSettled: () => queryClient.invalidateQueries(detailQuery),
  })
  const save = () => {
    if (draft !== null && draft !== body && !update.isPending)
      update.mutate(draft)
  }
  const { owner, repo, number } = pr
  const proxyImage = useCallback(
    (src: string) => reviewImageProxyUrl(owner, repo, number, src),
    [owner, repo, number]
  )
  const editing = draft !== null
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
  }, [body, editing])
  const clamped = overflows && !expanded
  return (
    <article aria-label="Description" className="group/description">
      <header className="mb-space-2 flex flex-wrap items-center gap-space-2 text-xs text-secondary">
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
          opened this {createdAt && <RelativeTimestamp value={createdAt} />}
        </a>
        <span className="ml-auto">{labels}</span>
        {draft === null && (
          <Button
            ref={editButton}
            size="xs"
            color="secondary"
            variant="plain"
            disabled={update.isPending}
            onClick={() => setDraft(body)}
          >
            Edit description
          </Button>
        )}
      </header>
      {draft !== null && (
        <div className="flex flex-col gap-space-2">
          <Textarea
            size="sm"
            aria-label="PR description"
            autoFocus
            rows={12}
            resize="vertical"
            maxLength={65536}
            value={draft}
            onChange={setDraft}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                event.preventDefault()
                save()
              }
              if (event.key === "Escape") event.stopPropagation()
            }}
          />
          <div className="flex justify-end gap-space-2">
            <Button
              size="sm"
              color="secondary"
              variant="plain"
              onClick={closeEditor}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={draft === body || update.isPending}
              onClick={save}
            >
              Save description
            </Button>
          </div>
        </div>
      )}
      <div
        hidden={draft !== null}
        ref={ref}
        className={cn(
          "relative flow-root max-w-[100ch] text-xs leading-[1.65]",
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
      {overflows && draft === null && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="mt-space-1 inline-flex items-center gap-space-1 text-xs font-medium text-secondary hover:text-primary"
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
