import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { SettingsRow } from "@/components/AppShell"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { api } from "@/lib/api"
import type { AllowedSlackBot, SlackBotOption } from "@/lib/api"

const QUERY_KEY = ["allowedSlackBots"]

export function AllowedSlackBotsSection({ isAdmin }: { isAdmin: boolean }) {
  const [manual, setManual] = useState(false)
  const [botId, setBotId] = useState("")
  const [search, setSearch] = useState("")
  const [selected, setSelected] = useState<SlackBotOption | null>(null)
  const [environment, setEnvironment] = useState("")
  const qc = useQueryClient()
  const bots = useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.listAllowedSlackBots,
    enabled: isAdmin,
  })
  const directory = useQuery({
    queryKey: ["slackBotDirectory"],
    queryFn: api.listSlackBots,
    enabled: isAdmin,
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  const environments = useQuery({
    queryKey: ["environment-options"],
    queryFn: api.listEnvironmentOptions,
    enabled: isAdmin,
  })
  const add = useMutation({
    mutationFn: api.allowSlackBot,
    onSuccess: async () => {
      setBotId("")
      setSelected(null)
      setSearch("")
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
  const error = add.error || remove.error || bots.error || environments.error
  const selectedEnvironment = environments.data?.environments.find(
    (option) => option.slug === environment && (option.repos?.length ?? 0) > 0
  )
  const alreadyAllowed = (bot: SlackBotOption) =>
    bots.data?.some(
      (allowed) =>
        allowed.team_id === bot.team_id && allowed.bot_id === bot.bot_id
    ) ?? false
  const botToAdd = manual
    ? botId.trim()
    : selected && search === selected.name && !alreadyAllowed(selected)
      ? selected.user_id
      : ""

  return (
    <div aria-labelledby="allowed-slack-bots-heading">
      <div className="space-y-1 px-4 pt-4">
        <h3 id="allowed-slack-bots-heading" className="text-sm font-medium">
          Allowed bots
        </h3>
        <p className="text-xs/relaxed text-muted-foreground">
          Choose trusted bots that can start system threads by mentioning Open
          SWE. Each bot runs as Open SWE in its assigned environment.
        </p>
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (
            !botToAdd ||
            !selectedEnvironment ||
            pending ||
            bots.isPending ||
            bots.isError ||
            environments.isError
          )
            return
          remove.reset()
          add.mutate({ bot_id: botToAdd, environment })
        }}
        className="space-y-3 p-4"
      >
        <label
          htmlFor={manual ? "allowed-slack-bot-id" : "allowed-slack-bot-search"}
          className="text-xs font-medium"
        >
          {manual ? "Slack bot ID" : "Search Slack bots"}
        </label>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          {manual ? (
            <Input
              id="allowed-slack-bot-id"
              aria-describedby="allowed-slack-bot-help"
              placeholder="B0123456789 or U0123456789"
              value={botId}
              onChange={(event) => setBotId(event.target.value)}
              disabled={pending}
            />
          ) : (
            <Combobox
              openOnInputClick
              items={directory.data ?? []}
              value={selected}
              onValueChange={setSelected}
              inputValue={search}
              onInputValueChange={setSearch}
              itemToStringLabel={(bot: SlackBotOption) => bot.name}
              isItemEqualToValue={(a: SlackBotOption, b: SlackBotOption) =>
                a.team_id === b.team_id && a.bot_id === b.bot_id
              }
              disabled={
                pending ||
                directory.isPending ||
                directory.isError ||
                bots.isPending ||
                bots.isError
              }
            >
              <ComboboxInput
                id="allowed-slack-bot-search"
                aria-describedby="allowed-slack-bot-help"
                placeholder={
                  directory.isPending
                    ? "Loading Slack bots…"
                    : "Search by bot name…"
                }
                className="w-full"
                showClear
              />
              <ComboboxContent>
                <ComboboxEmpty>No matching bots found.</ComboboxEmpty>
                <ComboboxList>
                  {(bot: SlackBotOption) => (
                    <ComboboxItem
                      key={bot.bot_id}
                      value={bot}
                      disabled={alreadyAllowed(bot)}
                      className="py-2 pr-8"
                    >
                      <Avatar size="sm">
                        <AvatarImage src={bot.image_url} alt="" />
                        <AvatarFallback>
                          {bot.name.slice(0, 1).toUpperCase()}
                        </AvatarFallback>
                      </Avatar>
                      <span className="flex min-w-0 flex-col">
                        <span className="truncate">{bot.name}</span>
                        <span className="text-[10px] text-muted-foreground">
                          {bot.bot_id}
                        </span>
                      </span>
                      {alreadyAllowed(bot) && (
                        <span className="ml-auto text-[10px] text-muted-foreground">
                          Already allowed
                        </span>
                      )}
                    </ComboboxItem>
                  )}
                </ComboboxList>
              </ComboboxContent>
            </Combobox>
          )}
          <Button
            type="submit"
            size="sm"
            disabled={
              !botToAdd ||
              !selectedEnvironment ||
              pending ||
              bots.isPending ||
              bots.isError ||
              environments.isError
            }
          >
            {add.isPending ? "Verifying…" : "Allow bot"}
          </Button>
        </div>
        <div className="space-y-2">
          <label
            htmlFor="allowed-slack-bot-environment"
            className="text-xs font-medium"
          >
            Environment
          </label>
          <Select
            value={environment}
            onValueChange={(value) => setEnvironment(value ?? "")}
            disabled={pending || environments.isPending || environments.isError}
          >
            <SelectTrigger
              id="allowed-slack-bot-environment"
              className="w-full"
            >
              <SelectValue placeholder="Choose an environment">
                {selectedEnvironment?.name}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {environments.data?.environments.map((option) => (
                <SelectItem
                  key={option.slug}
                  value={option.slug}
                  disabled={!option.repos?.length}
                >
                  {option.name}
                  {!option.repos?.length ? " (no repositories)" : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            {selectedEnvironment
              ? `GitHub access: ${selectedEnvironment.repos?.join(", ")}`
              : "Select an environment with configured repositories to allow this bot."}
          </p>
        </div>
        <p
          id="allowed-slack-bot-help"
          className="text-xs text-muted-foreground"
        >
          Uses the Open SWE GitHub App with access limited to the environment’s
          repositories. Only explicit mentions trigger runs; message edits are
          ignored.
        </p>
        {!manual && directory.error && (
          <div
            className="flex items-center gap-2 text-xs text-destructive"
            role="alert"
          >
            <span>{directory.error.message}</span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={directory.isFetching}
              onClick={() => void directory.refetch()}
            >
              Retry
            </Button>
          </div>
        )}
        <Button
          type="button"
          variant="link"
          size="sm"
          className="h-auto p-0 text-xs text-muted-foreground"
          disabled={pending}
          onClick={() => {
            setManual(!manual)
            setBotId("")
            setSelected(null)
            setSearch("")
            add.reset()
            remove.reset()
          }}
        >
          {manual ? "Browse Slack bots" : "Enter bot ID manually"}
        </Button>
      </form>
      {error && (
        <p role="alert" className="px-4 pb-3 text-xs text-destructive">
          {error.message}
        </p>
      )}
      {bots.isPending && (
        <p className="px-4 pb-4 text-xs text-muted-foreground">
          Loading allowed bots…
        </p>
      )}
      {bots.data?.length === 0 && (
        <p className="px-4 pb-4 text-xs text-muted-foreground">
          No Slack bots are allowed.
        </p>
      )}
      {bots.data?.map((bot) => (
        <SettingsRow
          key={`${bot.team_id}:${bot.bot_id}`}
          label={bot.name}
          description={
            bot.environment
              ? `${bot.bot_id} · Runs as Open SWE · ${environments.data?.environments.find((option) => option.slug === bot.environment)?.name ?? bot.environment}`
              : `${bot.bot_id} · Inactive — remove and add with an environment`
          }
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
    </div>
  )
}
