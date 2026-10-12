import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { DropdownMenuItem } from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { useMutation } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { toast } from "sonner"

import { SplitButton } from "@/components/SplitButton"
import { api } from "@/lib/api"

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
        <SplitButton
          size="xs"
          variant="plain"
          disabled={thread.isPending || thread.isSuccess}
          onClick={() => thread.mutate()}
          menuLabel="Agent options"
          menuDisabled={false}
          menuAlign="start"
          menu={
            <DropdownMenuItem onSelect={() => setMessageOpen(true)}>
              Send a message…
            </DropdownMenuItem>
          }
        >
          {thread.isPending ? "Opening thread…" : "Agent"}
        </SplitButton>
        <Dialog open={messageOpen} onOpenChange={setMessageOpen}>
          <DialogContent
            title="Send a message to the agent"
            description={`${repo}#${number} · ${title}`}
          >
            <form
              className="flex flex-col gap-space-4"
              onSubmit={(event) => {
                event.preventDefault()
                if (message.trim() && !send.isPending) send.mutate()
              }}
            >
              <Textarea
                size="md"
                autoFocus
                aria-label="Message"
                placeholder="What should the agent do?"
                value={message}
                maxLength={10000}
                disabled={send.isPending}
                onChange={setMessage}
              />
              <div className="flex justify-end gap-space-2">
                <Button
                  color="secondary"
                  variant="plain"
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
          </DialogContent>
        </Dialog>
        {!onReviewPage && (
          <>
            <Button
              size="xs"
              color="secondary"
              variant="plain"
              as={
                <Link
                  to="/agents/reviews/$owner/$repo/$number"
                  params={{
                    owner: owner!,
                    repo: name!,
                    number: String(number),
                  }}
                />
              }
            >
              Reviewer
            </Button>
            <IconButton
              asChild
              icon={GithubLogoIcon}
              label="GitHub"
              size="xs"
              color="secondary"
              variant="plain"
              tooltipProps={{ title: "Open on GitHub" }}
            >
              <a
                href={`https://github.com/${repo}/pull/${number}`}
                target="_blank"
                rel="noreferrer"
              />
            </IconButton>
          </>
        )}
      </span>
    </div>
  )
}
