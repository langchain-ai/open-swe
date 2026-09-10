import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import type { AllowedSlackBot } from "@/lib/api"

const QUERY_KEY = ["allowedSlackBots"]

export function AllowedSlackBotsSection({ isAdmin }: { isAdmin: boolean }) {
  const [botId, setBotId] = useState("")
  const qc = useQueryClient()
  const bots = useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.listAllowedSlackBots,
    enabled: isAdmin,
  })
  const add = useMutation({
    mutationFn: api.allowSlackBot,
    onSuccess: async () => {
      setBotId("")
      await qc.invalidateQueries({ queryKey: QUERY_KEY })
    },
  })
  const remove = useMutation({
    mutationFn: (bot: AllowedSlackBot) =>
      api.removeAllowedSlackBot(bot.team_id, bot.bot_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })

  if (!isAdmin) return null

  const pending = add.isPending || remove.isPending
  const error = add.error || remove.error || bots.error

  return (
    <SettingsSection
      title="Allowed Slack bots"
      description="Allow trusted bots to start Open SWE runs by mentioning it. Each bot runs with the GitHub permissions of the admin who adds it."
    >
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (!botId.trim() || pending) return
          remove.reset()
          add.mutate(botId.trim())
        }}
        className="space-y-3 p-4"
      >
        <label htmlFor="allowed-slack-bot-id" className="text-xs font-medium">
          Slack bot ID
        </label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            id="allowed-slack-bot-id"
            aria-describedby="allowed-slack-bot-help"
            placeholder="B0123456789 or U0123456789"
            value={botId}
            onChange={(event) => setBotId(event.target.value)}
            disabled={pending}
          />
          <Button
            type="submit"
            size="sm"
            disabled={
              !botId.trim() || pending || bots.isPending || bots.isError
            }
          >
            {add.isPending ? "Verifying…" : "Allow bot"}
          </Button>
        </div>
        <p
          id="allowed-slack-bot-help"
          className="text-xs text-muted-foreground"
        >
          Copy the bot’s member ID from its Slack profile, or enter its bot ID.
          Only explicit mentions trigger runs; message edits are ignored.
        </p>
      </form>
      {error && (
        <p role="alert" className="px-4 py-3 text-xs text-destructive">
          {error.message}
        </p>
      )}
      {bots.isPending && (
        <p className="px-4 py-3 text-xs text-muted-foreground">
          Loading allowed bots…
        </p>
      )}
      {bots.data?.length === 0 && (
        <p className="px-4 py-3 text-xs text-muted-foreground">
          No Slack bots are allowed.
        </p>
      )}
      {bots.data?.map((bot) => (
        <SettingsRow
          key={`${bot.team_id}:${bot.bot_id}`}
          label={bot.name}
          description={`${bot.bot_id} · Runs as ${bot.github_login}`}
          control={
            <Button
              size="sm"
              variant="outline"
              aria-label={`Remove ${bot.name}`}
              disabled={pending}
              onClick={() => {
                add.reset()
                remove.mutate(bot)
              }}
            >
              Remove
            </Button>
          }
        />
      ))}
    </SettingsSection>
  )
}
