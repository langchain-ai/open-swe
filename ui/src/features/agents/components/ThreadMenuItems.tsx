import { PushPinRegularIcon } from "@langchain/macaw-components/icons"
import { ArchiveIcon } from "@phosphor-icons/react/dist/ssr/Archive"
import { ArrowCounterClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowCounterClockwise"
import { BookOpenTextIcon } from "@phosphor-icons/react/dist/ssr/BookOpenText"
import { CopyIcon } from "@phosphor-icons/react/dist/ssr/Copy"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { LightningIcon } from "@phosphor-icons/react/dist/ssr/Lightning"
import { LinkSimpleIcon } from "@phosphor-icons/react/dist/ssr/LinkSimple"
import { SparkleIcon } from "@phosphor-icons/react/dist/ssr/Sparkle"
import { Link } from "@tanstack/react-router"
import { PushPinSlashIcon } from "@phosphor-icons/react/dist/ssr/PushPinSlash"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"
import { TreeStructureIcon } from "@phosphor-icons/react/dist/ssr/TreeStructure"

import { ContextMenuItem, DropdownMenuItem } from "@/components/MenuItem"
import { threadPullRequest } from "@/features/agents/lib/threadPullRequest"
import type { AgentThread, ReviewPageRef } from "@/features/agents/lib/types"
import {
  reviewPagePath,
  reviewPageRoute,
} from "@/features/reviews/lib/reviewEntry"
import { copyAppLinks, copyText } from "@/lib/clipboard"
import { useAltKey } from "@/lib/useAltKey"

/** Radix scopes items to their menu, so callers name the menu they render in. */
export type ThreadMenuKind = "dropdown" | "context"

const MENU_ITEM = {
  dropdown: DropdownMenuItem,
  context: ContextMenuItem,
} as const

const ICON_SIZE = 14

/**
 * Opens or copies the thread's pull request on our review page; holding Alt
 * (Option) switches both to GitHub. The GitHub variant is not an anchor
 * because an Alt-click on a link downloads it.
 */
function PullRequestMenuItems({
  menu,
  pullRequest,
}: {
  menu: ThreadMenuKind
  pullRequest: { url: string; review: ReviewPageRef }
}) {
  const Item = MENU_ITEM[menu]
  const github = useAltKey()
  return (
    <>
      {github ? (
        <Item
          onSelect={() =>
            window.open(pullRequest.url, "_blank", "noopener,noreferrer")
          }
        >
          <GithubLogoIcon size={ICON_SIZE} weight="regular" />
          Open PR on GitHub
        </Item>
      ) : (
        <Item asChild title="Hold ⌥ Option / Alt to open it on GitHub">
          <Link {...reviewPageRoute(pullRequest.review)}>
            <BookOpenTextIcon size={ICON_SIZE} weight="regular" />
            Open PR review
          </Link>
        </Item>
      )}
      <Item
        onSelect={() =>
          github
            ? copyText(pullRequest.url, "Copied the GitHub link")
            : copyAppLinks(
                [reviewPagePath(pullRequest.review)],
                "Copied the review link"
              )
        }
      >
        {github ? (
          <GithubLogoIcon size={ICON_SIZE} weight="regular" />
        ) : (
          <LinkSimpleIcon size={ICON_SIZE} weight="regular" />
        )}
        {github ? "Copy GitHub PR link" : "Copy PR review link"}
      </Item>
    </>
  )
}

export function ThreadMenuItems({
  menu = "dropdown",
  thread,
  pinned,
  archived,
  isDeleting,
  regeneratingTitle = false,
  onTogglePin,
  onToggleArchived,
  onRegenerateTitle,
  onDelete,
}: {
  menu?: ThreadMenuKind
  thread: AgentThread
  pinned: boolean
  archived: boolean
  isDeleting: boolean
  regeneratingTitle?: boolean
  onTogglePin: () => void
  onToggleArchived: () => void
  onRegenerateTitle?: () => void
  onDelete: () => void
}) {
  const Item = MENU_ITEM[menu]
  const pullRequest = threadPullRequest(thread)
  const slackUrl = thread.sourceAppUrl ?? thread.sourceUrl
  return (
    <>
      {pullRequest && (
        <PullRequestMenuItems menu={menu} pullRequest={pullRequest} />
      )}
      {thread.traceUrl && (
        <>
          <Item asChild>
            <a href={thread.traceUrl} target="_blank" rel="noreferrer">
              <TreeStructureIcon size={ICON_SIZE} weight="regular" />
              Open trace
            </a>
          </Item>
          <Item
            onSelect={() =>
              thread.traceUrl &&
              copyText(thread.traceUrl, "Copied the trace link")
            }
          >
            <LinkSimpleIcon size={ICON_SIZE} weight="regular" />
            Copy trace link
          </Item>
        </>
      )}
      {thread.sourceUrl && (
        <>
          <Item asChild>
            <a href={slackUrl ?? undefined} target="_blank" rel="noreferrer">
              <SlackLogoIcon size={ICON_SIZE} weight="regular" />
              Open in Slack
            </a>
          </Item>
          <Item
            onSelect={() =>
              thread.sourceUrl &&
              copyText(thread.sourceUrl, "Copied the Slack link")
            }
          >
            <LinkSimpleIcon size={ICON_SIZE} weight="regular" />
            Copy Slack link
          </Item>
        </>
      )}
      {thread.automationId && (
        <Item asChild>
          <Link
            to="/agents/automations/$scheduleId"
            params={{ scheduleId: thread.automationId }}
          >
            <LightningIcon size={ICON_SIZE} weight="regular" />
            Open automation
          </Link>
        </Item>
      )}
      <Item onSelect={onTogglePin}>
        {pinned ? (
          <PushPinSlashIcon size={ICON_SIZE} weight="regular" />
        ) : (
          <PushPinRegularIcon size={ICON_SIZE} />
        )}
        {pinned ? "Unpin thread" : "Pin thread"}
      </Item>
      {onRegenerateTitle && (
        <Item disabled={regeneratingTitle} onSelect={onRegenerateTitle}>
          <SparkleIcon size={ICON_SIZE} weight="regular" />
          {regeneratingTitle ? "Regenerating title…" : "Regenerate title"}
        </Item>
      )}
      <Item
        disabled={!thread.sandboxId}
        onSelect={() => {
          if (thread.sandboxId) {
            void navigator.clipboard.writeText(thread.sandboxId)
          }
        }}
        title={thread.sandboxId ?? undefined}
      >
        <CopyIcon size={ICON_SIZE} weight="regular" />
        Copy sandbox ID
      </Item>
      <Item
        onSelect={() => {
          void navigator.clipboard.writeText(thread.id)
        }}
        title={thread.id}
      >
        <CopyIcon size={ICON_SIZE} weight="regular" />
        Copy thread ID
      </Item>
      <Item onSelect={onToggleArchived}>
        {archived ? (
          <ArrowCounterClockwiseIcon size={ICON_SIZE} weight="regular" />
        ) : (
          <ArchiveIcon size={ICON_SIZE} weight="regular" />
        )}
        {archived ? "Unarchive thread" : "Archive thread"}
      </Item>
      <Item
        className="text-error-secondary"
        onSelect={onDelete}
        disabled={isDeleting}
      >
        <TrashIcon size={ICON_SIZE} weight="regular" />
        Delete thread
      </Item>
    </>
  )
}
