import { Link as MacawLink } from "@langchain/macaw-components/Link"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { ArrowsClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowsClockwise"
import { GitBranchIcon } from "@phosphor-icons/react/dist/ssr/GitBranch"
import { GitMergeIcon } from "@phosphor-icons/react/dist/ssr/GitMerge"
import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/ssr/PencilSimple"
import { XCircleIcon } from "@phosphor-icons/react/dist/ssr/XCircle"
import { useQuery } from "@tanstack/react-query"
import type { ReactNode } from "react"

import type {
  ConversationCommit,
  HistoryEvent,
  HistoryForcePush,
  HistoryStateChange,
} from "@/features/reviews/lib/conversationApi"
import { githubUrls } from "@/features/reviews/lib/githubUrls"
import { displayName } from "@/features/reviews/lib/logins"
import { cn, INHERITED_LINK } from "@/lib/utils"
import { CommitsBlock } from "./Discussion"
import { formatWhen } from "./notes/Byline"
import { reviewQueries, type PullRequestRef } from "./queries"

type Entry =
  | { kind: "commits"; key: string; commits: Array<ConversationCommit> }
  | {
      kind: "event"
      key: string
      event: Exclude<HistoryEvent, ConversationCommit>
    }

const STATE_WORDS: Record<
  HistoryStateChange["state"],
  { verb: string; icon: IconComponent }
> = {
  draft: { verb: "converted this to a draft", icon: PencilSimpleIcon },
  ready: { verb: "marked this ready for review", icon: GitPullRequestIcon },
  closed: { verb: "closed this", icon: XCircleIcon },
  reopened: { verb: "reopened this", icon: GitPullRequestIcon },
  merged: { verb: "merged this", icon: GitMergeIcon },
}

/** Back-to-back commits read as one push, as on GitHub. */
function toEntries(events: ReadonlyArray<HistoryEvent>): Array<Entry> {
  const entries: Array<Entry> = []
  events.forEach((event, index) => {
    const last = entries.at(-1)
    if (event.kind !== "commit")
      entries.push({ kind: "event", key: `e-${index}`, event })
    else if (last?.kind === "commits") last.commits.push(event)
    else
      entries.push({ kind: "commits", key: `c-${event.sha}`, commits: [event] })
  })
  return entries
}

/** Every push, rewrite and state change the pull request went through, oldest first. */
export function History({ pr }: { pr: PullRequestRef }) {
  const history = useQuery(reviewQueries.history(pr))
  const entries = toEntries(history.data?.events ?? [])
  return (
    <section
      aria-label="History"
      className="h-full min-h-0 overflow-y-auto px-space-4 py-space-4"
    >
      {history.isPending ? (
        <div className="flex flex-col gap-space-3">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-5 w-full" />
          ))}
        </div>
      ) : history.isError ? (
        <p className="text-xs text-error-secondary">
          Couldn&apos;t load the history: {history.error.message}
        </p>
      ) : entries.length === 0 ? (
        <p className="text-xs text-secondary">Nothing has happened yet.</p>
      ) : (
        <ol className="relative flex flex-col gap-space-3 before:absolute before:inset-y-2 before:left-[11px] before:border-l before:border-default">
          {entries.map((entry) =>
            entry.kind === "commits" ? (
              <CommitsBlock key={entry.key} commits={entry.commits} />
            ) : (
              <EventRow key={entry.key} pr={pr} event={entry.event} />
            )
          )}
        </ol>
      )}
    </section>
  )
}

function EventRow({
  pr,
  event,
}: {
  pr: PullRequestRef
  event: Exclude<HistoryEvent, ConversationCommit>
}) {
  const { icon: EventIcon, what } =
    event.kind === "force_push"
      ? { icon: ArrowsClockwiseIcon, what: <ForcePush pr={pr} push={event} /> }
      : event.kind === "base_change"
        ? {
            icon: GitBranchIcon,
            what: (
              <>
                changed the base from <Ref>{event.previous}</Ref> to{" "}
                <Ref>{event.current}</Ref>
              </>
            ),
          }
        : {
            icon: STATE_WORDS[event.state].icon,
            what: STATE_WORDS[event.state].verb,
          }
  return (
    <li className="relative pl-space-6 text-xs text-secondary">
      <span className="absolute top-0.5 left-[5px] flex size-[13px] items-center justify-center rounded-full bg-surface-level-1 ring-4 ring-surface-level-1">
        <EventIcon className="size-3.5" />
      </span>
      <span className="font-medium text-primary">
        {displayName(event.author)}
      </span>{" "}
      {what}{" "}
      <span title={new Date(event.created_at).toLocaleString()}>
        {formatWhen(event.created_at)}
      </span>
    </li>
  )
}

function ForcePush({
  pr,
  push,
}: {
  pr: PullRequestRef
  push: HistoryForcePush
}) {
  const branch = push.ref === "head" ? "the branch" : "the base branch"
  if (!push.before || !push.after) return <>force-pushed {branch}</>
  return (
    <>
      force-pushed {branch}{" "}
      <MacawLink
        variant="xs"
        href={githubUrls.compare(pr, push.before, push.after)}
        target="_blank"
        rel="noreferrer"
        title="Compare on GitHub"
        className={cn(INHERITED_LINK, "whitespace-nowrap hover:underline")}
      >
        <Sha sha={push.before} /> → <Sha sha={push.after} />
      </MacawLink>
    </>
  )
}

function Sha({ sha }: { sha: string }) {
  return <Ref>{sha.slice(0, 7)}</Ref>
}

function Ref({ children }: { children: ReactNode }) {
  return <code className="font-mono text-xxs text-primary">{children}</code>
}
