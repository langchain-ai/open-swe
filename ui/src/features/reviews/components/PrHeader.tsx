import type { ReactNode } from "react"

import { RecordHeader } from "@langchain/gtm-platform-design-system/patterns/record-header"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"

import { GitHub, GitMerge, GitPullRequest } from "@/components/glyphs"

type PrState = "open" | "draft" | "merged" | "closed"

const STATE_TONE = {
  open: "positive",
  draft: "neutral",
  closed: "risk",
} as const

function isPrState(state: string): state is PrState {
  return (
    state === "open" ||
    state === "draft" ||
    state === "merged" ||
    state === "closed"
  )
}

/** The PR's lifecycle as one Quiet badge; merged wears the app's merged pair. */
export function PrStateBadge({ state }: { state: string }) {
  const known: PrState = isPrState(state) ? state : "open"
  if (known === "merged")
    return (
      <Badge
        tier="quiet"
        className="border-merged/20 bg-merged-bg text-merged capitalize"
      >
        <Icon icon={GitMerge} size="sm" />
        {state}
      </Badge>
    )
  return (
    <Badge tier="quiet" tone={STATE_TONE[known]} className="capitalize">
      <Icon icon={GitPullRequest} size="sm" />
      {state}
    </Badge>
  )
}

function Ref({ name }: { name: string }) {
  return (
    <Box
      render={<span />}
      title={name}
      radius="badge"
      border="line"
      className="max-w-60 truncate px-1.5 font-mono text-meta text-ink-muted"
    >
      {name}
    </Box>
  )
}

export interface PrHeaderProps {
  url: string
  title: string
  state: string
  headRef: string
  baseRef: string
  number?: number | null
  author?: string | null
  stats?: {
    changedFiles: number
    additions: number
    deletions: number
  } | null
  /** The PR's own verbs, under its facts. */
  children?: ReactNode
}

/** PR identity in the work pane: title, lifecycle, and the quiet facts under it. */
export function PrHeader({
  url,
  title,
  state,
  headRef,
  baseRef,
  number,
  author,
  stats,
  children,
}: PrHeaderProps) {
  return (
    <RecordHeader
      title={
        <Inline gap="sm" className="min-w-0">
          <PrStateBadge state={state} />
          <Box
            render={<h1 />}
            className="min-w-0 text-title font-semibold break-words text-ink"
          >
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className="hover:underline"
            >
              <Icon
                icon={GitHub}
                size="md"
                label="GitHub"
                className="mr-1.5 inline align-middle text-ink-subtle"
              />
              {title}
              {number != null && (
                <span className="font-normal text-ink-subtle"> #{number}</span>
              )}
            </a>
          </Box>
        </Inline>
      }
      meta={
        <Stack gap="md">
          <Inline gap="sm" wrap className="text-meta text-ink-subtle">
            {author && <span className="font-medium text-ink">{author}</span>}
            <Ref name={baseRef} />
            <span aria-hidden="true">←</span>
            <Ref name={headRef} />
            {stats && (
              <Inline gap="sm" className="font-mono tabular-nums">
                <span>
                  {stats.changedFiles} file{stats.changedFiles === 1 ? "" : "s"}
                </span>
                <span className="text-positive">+{stats.additions}</span>
                <span className="text-risk">−{stats.deletions}</span>
              </Inline>
            )}
          </Inline>
          {children}
        </Stack>
      }
    />
  )
}
