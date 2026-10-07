import { Link, useNavigate } from "@tanstack/react-router"
import { useMutation } from "@tanstack/react-query"
import { IoLogoGithub } from "react-icons/io5"
import { useState } from "react"
import { ChevronDown } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Menu, MenuTrigger, MenuPopup, MenuItem } from "@/components/ui/menu"
import {
  Dialog,
  DialogPopup,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { Textarea } from "@/components/ui/textarea"

import { buttonVariants } from "@/components/ui/button"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

export const navLink = cn(
  buttonVariants({ variant: "ghost", size: "sm" }),
  "px-1.5 text-muted-foreground"
)

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
  return (
    <div className="text-xs">
      <span className="flex flex-wrap items-center gap-0.5">
        <span className="inline-flex items-center">
          <button
            type="button"
            className={navLink}
            disabled={thread.isPending || thread.isSuccess}
            aria-live="polite"
            onClick={() => thread.mutate()}
          >
            {thread.isPending ? "Opening thread…" : "Agent"}
          </button>
          <Menu>
            <MenuTrigger
              className={cn(navLink, "border-l border-border px-1")}
              aria-label="Agent options"
            >
              <ChevronDown className="size-3" />
            </MenuTrigger>
            <MenuPopup align="start">
              <MenuItem onClick={() => setMessageOpen(true)}>
                Send a message…
              </MenuItem>
            </MenuPopup>
          </Menu>
        </span>
        <Dialog open={messageOpen} onOpenChange={setMessageOpen}>
          <DialogPopup className="gap-4 p-5">
            <DialogTitle>Send a message to the agent</DialogTitle>
            <DialogDescription>
              {repo}#{number} · {title}
            </DialogDescription>
            <form
              className="flex flex-col gap-4"
              onSubmit={(event) => {
                event.preventDefault()
                if (message.trim() && !send.isPending) send.mutate()
              }}
            >
              <Textarea
                autoFocus
                aria-label="Message"
                placeholder="What should the agent do?"
                value={message}
                maxLength={10000}
                disabled={send.isPending}
                onChange={(event) => setMessage(event.target.value)}
              />
              <div className="flex justify-end gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setMessageOpen(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={!message.trim() || send.isPending}
                >
                  {send.isPending ? "Sending…" : "Send message"}
                </Button>
              </div>
            </form>
          </DialogPopup>
        </Dialog>
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
              className={navLink}
              href={`https://github.com/${repo}/pull/${number}`}
              target="_blank"
              rel="noreferrer"
              aria-label="GitHub"
              title="Open on GitHub"
            >
              <IoLogoGithub className="size-3.5" />
            </a>
          </>
        )}
      </span>
    </div>
  )
}
