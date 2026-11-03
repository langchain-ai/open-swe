import { PlusIcon } from "@langchain/macaw-components/icons"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { Input } from "@langchain/macaw-components/Input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { Text } from "@langchain/macaw-components/Text"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { useState } from "react"

import { api } from "@/lib/api"
import type { AllowedGitHubBot } from "@/lib/api"

const QUERY_KEY = ["allowedGitHubBots"]

export function AllowedGitHubBotsSection() {
  const [open, setOpen] = useState(false)
  const [login, setLogin] = useState("")
  const qc = useQueryClient()
  const bots = useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.listAllowedGitHubBots,
  })
  const add = useMutation({
    meta: { silent: true },
    mutationFn: api.allowGitHubBot,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: QUERY_KEY })
      setOpen(false)
      setLogin("")
    },
  })
  const remove = useMutation({
    meta: { errorTitle: "Couldn't remove GitHub bot" },
    mutationFn: (bot: AllowedGitHubBot) =>
      api.removeAllowedGitHubBot(bot.login),
    onMutate: async (bot) => {
      await qc.cancelQueries({ queryKey: QUERY_KEY })
      qc.setQueryData<Array<AllowedGitHubBot>>(QUERY_KEY, (current) =>
        current?.filter((b) => b.login !== bot.login)
      )
    },
    onError: (_error, bot) =>
      qc.setQueryData<Array<AllowedGitHubBot>>(QUERY_KEY, (current) =>
        current && !current.some((b) => b.login === bot.login)
          ? [...current, bot]
          : current
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })

  const pending = add.isPending
  const unavailable = pending || bots.isPending || bots.isError

  return (
    <div aria-labelledby="allowed-github-bots-heading" className="p-space-4">
      <div className="flex items-start justify-between gap-space-4">
        <div className="space-y-space-1">
          <h3
            id="allowed-github-bots-heading"
            className="text-sm font-medium text-primary"
          >
            GitHub bots
          </h3>
          <p className="text-xs/relaxed text-secondary">
            Treat pull requests opened by trusted GitHub bots like those opened
            by Open SWE users, so people can request reviews of them.
          </p>
        </div>
        <Popover
          open={open}
          onOpenChange={(value) => {
            if (pending) return
            setOpen(value)
            setLogin("")
            add.reset()
          }}
        >
          <PopoverTrigger asChild>
            <Button
              color="secondary"
              variant="outlined"
              leftDecorator={PlusIcon}
              disabled={unavailable}
            >
              Add GitHub bot
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-80 p-0">
            <Text
              as="h4"
              variant="sm"
              weight="medium"
              className="px-space-3 pt-space-3 pb-space-2"
            >
              Add a GitHub bot
            </Text>
            <form
              className="space-y-space-3 px-space-3 pb-space-3"
              onSubmit={(event) => {
                event.preventDefault()
                if (!login.trim() || unavailable) return
                add.mutate({ login: login.trim() })
              }}
            >
              <Input
                size="md"
                id="allowed-github-bot-login"
                label="GitHub bot login"
                placeholder="langsmith-engine[bot]"
                value={login}
                onChange={setLogin}
                disabled={pending}
              />
              <Button type="submit" disabled={!login.trim() || unavailable}>
                {pending ? "Allowing…" : "Allow bot"}
              </Button>
            </form>
            {add.error && (
              <p
                role="alert"
                className="px-space-3 pb-space-3 text-xs text-error-secondary"
              >
                {add.error.message}
              </p>
            )}
          </PopoverContent>
        </Popover>
      </div>
      {bots.error && (
        <div role="alert" className="mt-space-3">
          <Banner intent="error">{bots.error.message}</Banner>
        </div>
      )}
      {bots.isPending && (
        <p className="py-space-5 text-xs text-secondary">
          Loading allowed GitHub bots…
        </p>
      )}
      {bots.data?.length === 0 && (
        <EmptyState
          size="sm"
          icon={GithubLogoIcon}
          title="No GitHub bots are allowed."
          description="Add a GitHub App bot by its login."
          className="mt-space-4 rounded-lg border border-dashed border-default"
        />
      )}
      {!!bots.data?.length && (
        <ul className="mt-space-4 divide-y divide-default rounded-lg border border-default">
          {bots.data.map((bot) => (
            <li
              key={bot.login}
              className="flex items-center gap-space-3 px-space-3 py-space-3"
            >
              <GithubLogoIcon
                aria-hidden="true"
                size={20}
                weight="regular"
                className="text-icon-secondary"
              />
              <div className="min-w-0 flex-1">
                <p
                  className="truncate text-sm font-medium text-primary"
                  title={bot.login}
                >
                  {bot.login}
                </p>
                {bot.created_by && (
                  <p className="text-xs text-secondary">
                    Added by {bot.created_by}
                  </p>
                )}
              </div>
              <Button
                color="secondary"
                variant="plain"
                aria-label={`Remove ${bot.login}`}
                disabled={pending}
                onClick={() => {
                  add.reset()
                  remove.mutate(bot)
                }}
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
