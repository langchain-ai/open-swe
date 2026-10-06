import { Link, useNavigate } from "@tanstack/react-router"
import { useMutation } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import {
  Button,
  buttonVariants,
} from "@langchain/gtm-platform-design-system/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@langchain/gtm-platform-design-system/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"

import {
  Bot,
  ChevronDown,
  GitHub,
  MessageSquare,
  MoreVertical,
} from "@/components/glyphs"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

/** A link drawn as a quiet compact control, for hops that leave the row. */
export const navLink = cn(
  buttonVariants({ variant: "ghost", size: "compact" }),
  "px-2 text-ink-subtle hover:text-ink"
)

function usePullRequestAgent(repo: string, number: number, title: string) {
  const navigate = useNavigate()
  const [messageOpen, setMessageOpen] = useState(false)
  const [message, setMessage] = useState("")
  const send = useMutation({
    mutationFn: () =>
      api.messagePullRequestThread(repo, number, title, message.trim()),
    meta: { errorTitle: "Couldn't send message" },
    onSuccess: ({ thread_id, already_running }) => {
      setMessageOpen(false)
      setMessage("")
      toast.success(
        already_running
          ? "Message queued for the agent"
          : "Message sent to the agent",
        {
          action: {
            label: "Open thread",
            onClick: () =>
              navigate({
                to: "/agents/$threadId",
                params: { threadId: thread_id },
              }),
          },
        }
      )
    },
    retry: false,
  })
  const thread = useMutation({
    mutationFn: () => api.openPullRequestThread(repo, number, title),
    meta: { errorTitle: "Couldn't open agent thread" },
    onSuccess: ({ thread_id }) =>
      navigate({ to: "/agents/$threadId", params: { threadId: thread_id } }),
    retry: false,
  })
  const messageDialog = (
    <Dialog open={messageOpen} onOpenChange={setMessageOpen}>
      <DialogContent className="sm:max-w-md">
        <form
          className="contents"
          onSubmit={(event) => {
            event.preventDefault()
            if (message.trim() && !send.isPending) send.mutate()
          }}
        >
          <DialogHeader>
            <DialogTitle>Send a message to the agent</DialogTitle>
            <DialogDescription>
              {repo}#{number} · {title}
            </DialogDescription>
          </DialogHeader>
          <Textarea
            autoFocus
            aria-label="Message"
            placeholder="What should the agent do?"
            value={message}
            maxLength={10000}
            disabled={send.isPending}
            onChange={(event) => setMessage(event.target.value)}
          />
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setMessageOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!message.trim()}
              loading={send.isPending}
            >
              Send message
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
  return { thread, openMessage: () => setMessageOpen(true), messageDialog }
}

export function PullRequestLinks({
  repo,
  number,
  title,
  onReviewPage = false,
}: {
  repo: string
  number: number
  title: string
  onReviewPage?: boolean
}) {
  const [owner, name] = repo.split("/")
  const { thread, openMessage, messageDialog } = usePullRequestAgent(
    repo,
    number,
    title
  )
  return (
    <Inline gap="xs" wrap className="text-label">
      <Inline>
        <Button
          size="compact"
          variant="ghost"
          className="rounded-r-none px-2 text-ink-subtle hover:text-ink"
          disabled={thread.isPending || thread.isSuccess}
          aria-live="polite"
          onClick={() => thread.mutate()}
        >
          {thread.isPending ? "Opening thread…" : "Agent"}
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                size="icon-sm"
                variant="ghost"
                className="rounded-l-none text-ink-subtle hover:text-ink"
              />
            }
            aria-label="Agent options"
          >
            <Icon icon={ChevronDown} size="sm" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuItem onClick={openMessage}>
              Send a message…
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </Inline>
      {messageDialog}
      {!onReviewPage && (
        <>
          <Link
            className={navLink}
            to="/agents/reviews/$owner/$repo/$number"
            params={{ owner: owner!, repo: name!, number: String(number) }}
          >
            Reviewer
          </Link>
          <a
            className={cn(
              buttonVariants({ variant: "ghost", size: "icon-sm" }),
              "text-ink-subtle hover:text-ink"
            )}
            href={`https://github.com/${repo}/pull/${number}`}
            target="_blank"
            rel="noreferrer"
            aria-label="GitHub"
            title="Open on GitHub"
          >
            <Icon icon={GitHub} size="sm" />
          </a>
        </>
      )}
    </Inline>
  )
}

/** The same agent and GitHub hops as a queue row's one menu. */
export function PullRequestRowMenu({
  repo,
  number,
  title,
}: {
  repo: string
  number: number
  title: string
}) {
  const { thread, openMessage, messageDialog } = usePullRequestAgent(
    repo,
    number,
    title
  )
  // The row owns Enter, Space and clicks; this menu and its dialog keep theirs.
  const keepFromRow = (event: { stopPropagation: () => void }) =>
    event.stopPropagation()
  return (
    <Box render={<span />} onKeyDown={keepFromRow} onClick={keepFromRow}>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={<Button size="icon-sm" variant="ghost" />}
          aria-label={`Actions for ${repo}#${number}`}
        >
          <Icon icon={MoreVertical} size="sm" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            disabled={thread.isPending || thread.isSuccess}
            onClick={() => thread.mutate()}
          >
            <Icon icon={Bot} size="sm" />
            {thread.isPending ? "Opening thread…" : "Open agent thread"}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={openMessage}>
            <Icon icon={MessageSquare} size="sm" />
            Send a message…
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onClick={() =>
              window.open(
                `https://github.com/${repo}/pull/${number}`,
                "_blank",
                "noopener,noreferrer"
              )
            }
          >
            <Icon icon={GitHub} size="sm" />
            Open on GitHub
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {messageDialog}
    </Box>
  )
}
