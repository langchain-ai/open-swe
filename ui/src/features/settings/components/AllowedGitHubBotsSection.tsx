import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus, Robot } from "@phosphor-icons/react"
import { useState } from "react"

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"

const QUERY_KEY = ["allowedGitHubBots"]

export function AllowedGitHubBotsSection() {
  const [login, setLogin] = useState("")
  const qc = useQueryClient()
  const bots = useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.listAllowedGitHubBots,
  })
  const add = useMutation({
    mutationFn: api.allowGitHubBot,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: QUERY_KEY })
      setLogin("")
    },
  })
  const remove = useMutation({
    mutationFn: api.removeAllowedGitHubBot,
    onSuccess: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })

  const pending = add.isPending || remove.isPending
  const unavailable = pending || bots.isPending || bots.isError
  const error = add.error || remove.error || bots.error

  return (
    <div aria-labelledby="allowed-github-bots-heading" className="p-4">
      <div className="space-y-1">
        <h3 id="allowed-github-bots-heading" className="text-sm font-medium">
          Allowed bots
        </h3>
        <p className="text-xs/relaxed text-muted-foreground">
          PR comments from registered Open SWE users and these bots can prompt
          the agent. Everyone else is ignored and left out of its context.
        </p>
      </div>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          if (!login.trim() || unavailable) return
          remove.reset()
          add.mutate({ login: login.trim() })
        }}
      >
        <Input
          aria-label="GitHub bot login"
          placeholder="dependabot[bot]"
          value={login}
          onChange={(event) => setLogin(event.target.value)}
          disabled={pending}
        />
        <Button
          type="submit"
          size="sm"
          variant="outline"
          disabled={!login.trim() || unavailable}
        >
          <Plus size={14} /> {add.isPending ? "Verifying…" : "Add bot"}
        </Button>
      </form>
      {error && (
        <p role="alert" className="mt-3 text-xs text-destructive">
          {error.message}
        </p>
      )}
      {bots.isPending && (
        <p className="py-6 text-xs text-muted-foreground">
          Loading allowed bots…
        </p>
      )}
      {bots.data?.length === 0 && (
        <div className="mt-4 flex items-center gap-3 rounded-lg border border-dashed px-4 py-5">
          <Robot size={22} className="shrink-0 text-muted-foreground" />
          <p className="text-xs font-medium">No GitHub bots are allowed.</p>
        </div>
      )}
      {!!bots.data?.length && (
        <ul className="mt-4 divide-y rounded-lg border">
          {bots.data.map((bot) => (
            <li
              key={bot.github_id}
              className="flex items-center gap-3 px-3 py-3"
            >
              <Avatar>
                <AvatarImage src={bot.avatar_url} alt="" />
                <AvatarFallback>
                  <Robot size={18} />
                </AvatarFallback>
              </Avatar>
              <p
                className="min-w-0 flex-1 truncate text-sm font-medium"
                title={bot.login}
              >
                {bot.login}
              </p>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Remove ${bot.login}`}
                disabled={pending}
                onClick={() => {
                  add.reset()
                  remove.mutate(bot.github_id)
                }}
              >
                {remove.isPending && remove.variables === bot.github_id
                  ? "Removing…"
                  : "Remove"}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-xs/relaxed text-muted-foreground">
        A bot’s comment runs as the thread’s owner.
      </p>
    </div>
  )
}
