import { PushPinRegularIcon } from "@langchain/macaw-components/icons"
import { ArchiveIcon } from "@phosphor-icons/react/dist/ssr/Archive"
import { ArrowCounterClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowCounterClockwise"
import { BookOpenTextIcon } from "@phosphor-icons/react/dist/ssr/BookOpenText"
import { CopyIcon } from "@phosphor-icons/react/dist/ssr/Copy"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { LightningIcon } from "@phosphor-icons/react/dist/ssr/Lightning"
import { SparkleIcon } from "@phosphor-icons/react/dist/ssr/Sparkle"
import { Link, useNavigate } from "@tanstack/react-router"
import { PushPinSlashIcon } from "@phosphor-icons/react/dist/ssr/PushPinSlash"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"
import { TreeStructureIcon } from "@phosphor-icons/react/dist/ssr/TreeStructure"

import {
  ContextMenuItem,
  DropdownMenuItem,
  LinkMenuItem,
} from "@/components/MenuItem"
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

const openExternal = (url: string) =>
  window.open(url, "_blank", "noopener,noreferrer")

/**
 * Opens the thread's pull request on our review page, with a button that copies
 * that link; holding Alt (Option) switches both to GitHub.
 */
function PullRequestMenuItem({
  menu,
  pullRequest,
}: {
  menu: ThreadMenuKind
  pullRequest: { url: string; review: ReviewPageRef }
}) {
  const navigate = useNavigate()
  const github = useAltKey()
  return (
    <LinkMenuItem
      item={MENU_ITEM[menu]}
      icon={
        github ? (
          <GithubLogoIcon size={ICON_SIZE} weight="regular" />
        ) : (
          <BookOpenTextIcon size={ICON_SIZE} weight="regular" />
        )
      }
      label={github ? "Open PR on GitHub" : "Open PR review"}
      title={github ? undefined : "Hold ⌥ Option / Alt for GitHub"}
      copyLabel={github ? "Copy GitHub PR link" : "Copy PR review link"}
      href={github ? undefined : reviewPagePath(pullRequest.review)}
      onOpen={() =>
        github
          ? openExternal(pullRequest.url)
          : void navigate(reviewPageRoute(pullRequest.review))
      }
      onCopy={() =>
        github
          ? copyText(pullRequest.url, "Copied the GitHub link")
          : copyAppLinks(
              [reviewPagePath(pullRequest.review)],
              "Copied the review link"
            )
      }
    />
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
        <PullRequestMenuItem menu={menu} pullRequest={pullRequest} />
      )}
      {thread.traceUrl && (
        <LinkMenuItem
          item={Item}
          icon={<TreeStructureIcon size={ICON_SIZE} weight="regular" />}
          label="Open trace"
          copyLabel="Copy trace link"
          href={thread.traceUrl}
          external
          onCopy={() =>
            thread.traceUrl &&
            copyText(thread.traceUrl, "Copied the trace link")
          }
        />
      )}
      {thread.sourceUrl && (
        <LinkMenuItem
          item={Item}
          icon={<SlackLogoIcon size={ICON_SIZE} weight="regular" />}
          label="Open in Slack"
          copyLabel="Copy Slack link"
          href={slackUrl ?? undefined}
          external
          onCopy={() =>
            thread.sourceUrl &&
            copyText(thread.sourceUrl, "Copied the Slack link")
          }
        />
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
