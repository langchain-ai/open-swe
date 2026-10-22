import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import type { ReactNode } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"

import type { ClaudeCodeSyncToken, MintedClaudeCodeSyncToken } from "@/lib/api"
import { SettingsSection } from "@/components/AppShell"
import { api } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { ConfirmDialog } from "./ConfirmDialog"

const TOKENS_KEY = ["claudeCodeSyncTokens"]
const SETTINGS_FILE = "~/.claude/settings.json"

function Code({ children }: { children: ReactNode }) {
  return (
    <code className="rounded-sm bg-surface-level-2 px-space-1 font-mono text-xs">
      {children}
    </code>
  )
}

function Step({
  number,
  title,
  children,
}: {
  number: number
  title: string
  children: ReactNode
}) {
  return (
    <li className="flex gap-space-3 px-space-4 py-space-4">
      <span
        aria-hidden
        className="flex size-6 shrink-0 items-center justify-center rounded-full bg-surface-level-2 text-xs font-medium text-primary"
      >
        {number}
      </span>
      <div className="min-w-0 flex-1 space-y-space-2">
        <p className="text-sm font-medium text-primary">{title}</p>
        <div className="space-y-space-3 text-sm text-secondary">{children}</div>
      </div>
    </li>
  )
}

function CopySettings({ token }: { token: MintedClaudeCodeSyncToken }) {
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const settings = JSON.stringify(token.settings, null, 2)
  return (
    <div className="space-y-space-2">
      <pre className="max-h-64 overflow-auto rounded-md bg-surface-level-2 p-space-3 font-mono text-xs text-primary">
        {settings}
      </pre>
      <Button
        size="xs"
        color="primary"
        onClick={() =>
          void navigator.clipboard
            .writeText(settings)
            .then(() => setCopied(true))
            .catch(() =>
              setError("Could not copy. Select the settings and copy them.")
            )
        }
      >
        {copied ? "Copied" : "Copy settings"}
      </Button>
      {error && (
        <p role="alert" className="text-sm text-error-secondary">
          {error}
        </p>
      )}
    </div>
  )
}

/** Connecting a machine: name it, paste its hooks into Claude Code, start a session. */
export function ClaudeCodeSetupSection() {
  const qc = useQueryClient()
  const [name, setName] = useState("")
  const [minted, setMinted] = useState<MintedClaudeCodeSyncToken | null>(null)
  const create = useMutation({
    meta: { errorTitle: "Couldn't create a Claude Code token" },
    mutationFn: (tokenName: string) => api.createClaudeCodeSyncToken(tokenName),
    onSuccess: (token) => {
      setMinted(token)
      setName("")
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: TOKENS_KEY }),
  })

  return (
    <SettingsSection
      title="Connect a machine"
      description="Do this once on each computer you use Claude Code on."
    >
      <ol className="divide-y divide-default">
        <Step number={1} title="Name this machine">
          <p>
            Pick a name you’ll recognize later, then choose Connect Claude Code.
            This creates the machine’s token.
          </p>
          <form
            className="flex flex-wrap items-end gap-space-3"
            onSubmit={(event) => {
              event.preventDefault()
              if (!create.isPending && name.trim()) create.mutate(name.trim())
            }}
          >
            <Input
              label="Machine name"
              size="md"
              className="w-auto"
              value={name}
              maxLength={120}
              placeholder="Work laptop"
              onChange={setName}
            />
            <Button
              type="submit"
              size="xs"
              color="primary"
              disabled={create.isPending || !name.trim()}
            >
              {create.isPending ? "Creating…" : "Connect Claude Code"}
            </Button>
          </form>
        </Step>
        <Step number={2} title="Add the settings to Claude Code">
          {minted ? (
            <>
              <p>
                Copy these settings now: they hold {minted.name}’s token, which
                won’t be shown again.
              </p>
              <CopySettings token={minted} />
            </>
          ) : (
            <p>The settings to copy appear here after step 1.</p>
          )}
          <ul className="list-disc space-y-space-1 pl-space-5">
            <li>
              No <Code>{SETTINGS_FILE}</Code> yet? Create it and paste the
              settings as the whole file.
            </li>
            <li>
              Already have one? Paste the <Code>"hooks"</Code> block inside its
              outer braces, with a comma after the entry before it.
            </li>
            <li>
              Already have hooks? Add these events next to yours, keeping both.
            </li>
          </ul>
        </Step>
        <Step number={3} title="Start a Claude Code session">
          <p>
            Open a new session and send a prompt. It shows up in your sidebar
            with a terminal icon, and the thread fills in as Claude works.
          </p>
          <p>
            Nothing appears? Run <Code>/hooks</Code> in Claude Code: you should
            see Open SWE’s hooks under eight events. If you don’t, check the
            file is valid JSON.
          </p>
        </Step>
      </ol>
    </SettingsSection>
  )
}

/** The machines that mirror their sessions, each revocable on its own. */
export function ClaudeCodeMachinesSection() {
  const qc = useQueryClient()
  const tokens = useQuery({
    queryKey: TOKENS_KEY,
    queryFn: api.listClaudeCodeSyncTokens,
  })
  const [revoking, setRevoking] = useState<ClaudeCodeSyncToken | null>(null)
  const revoke = useMutation({
    meta: { errorTitle: "Couldn't revoke the Claude Code token" },
    mutationFn: (token: ClaudeCodeSyncToken) =>
      api.revokeClaudeCodeSyncToken(token.id),
    onMutate: async (token) => {
      setRevoking(null)
      return {
        undo: await optimisticUpdate<ClaudeCodeSyncToken[]>(
          qc,
          TOKENS_KEY,
          (current) => current.filter((item) => item.id !== token.id)
        ),
      }
    },
    onError: (_e, _v, ctx) => ctx?.undo(),
    onSettled: () => void qc.invalidateQueries({ queryKey: TOKENS_KEY }),
  })

  return (
    <SettingsSection
      title="Machines"
      description="Each connected machine has its own token. Revoke one to stop that machine mirroring."
    >
      <div className="px-space-4 py-space-3">
        {tokens.isPending ? (
          <p className="text-sm text-secondary">Loading machines…</p>
        ) : tokens.isError ? (
          <p role="alert" className="text-sm text-error-secondary">
            {tokens.error.message}
          </p>
        ) : tokens.data.length === 0 ? (
          <p className="text-sm text-secondary">
            No machines are connected yet.
          </p>
        ) : (
          <ul className="divide-y divide-default">
            {tokens.data.map((token) => (
              <li
                key={token.id}
                className="flex flex-wrap items-center justify-between gap-space-3 py-space-3"
              >
                <div className="space-y-space-1">
                  <p className="text-sm font-medium text-primary">
                    {token.name}{" "}
                    <span className="font-mono text-xs text-secondary">
                      …{token.token_suffix}
                    </span>
                  </p>
                  <p className="text-xs text-secondary">
                    Connected {new Date(token.created_at).toLocaleString()} ·
                    Last used{" "}
                    {token.last_used_at
                      ? new Date(token.last_used_at).toLocaleString()
                      : "never"}
                  </p>
                </div>
                <Button
                  size="xs"
                  color="error"
                  aria-label={`Revoke ${token.name}`}
                  onClick={() => setRevoking(token)}
                >
                  Revoke<span className="sr-only"> {token.name}</span>
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <ConfirmDialog
        open={!!revoking}
        onOpenChange={(open) => {
          if (!open) setRevoking(null)
        }}
        title={`Revoke ${revoking?.name}?`}
        description="Claude Code on that machine stops mirroring its sessions. Threads it already mirrored stay."
        confirmLabel="Revoke token"
        pendingLabel="Revoking…"
        pending={revoke.isPending}
        destructive
        onConfirm={() => revoking && revoke.mutate(revoking)}
      />
    </SettingsSection>
  )
}

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-space-1 px-space-4 py-space-3 sm:flex-row sm:gap-space-6">
      <dt className="text-sm font-medium text-primary sm:w-40 sm:shrink-0">
        {term}
      </dt>
      <dd className="text-sm text-secondary">{children}</dd>
    </div>
  )
}

/** What a machine's token is and what mirroring shows, in plain language. */
export function ClaudeCodeAboutSection() {
  return (
    <>
      <SettingsSection title="About the token">
        <dl className="divide-y divide-default">
          <Fact term="What it is">
            A secret starting with <Code>occ_</Code>. Claude Code sends it with
            every hook event so Open SWE knows the session is yours. Open SWE
            keeps only a hash of it, which is why it’s shown once.
          </Fact>
          <Fact term="Where it lives">
            In plain text in <Code>{SETTINGS_FILE}</Code> on that machine, in
            each hook’s <Code>Authorization</Code> header. Keep the file out of
            shared repositories and backups other people can read.
          </Fact>
          <Fact term="What it can do">
            Only add events to your own mirrored threads. It can’t read threads,
            start or steer agents, or reach anyone else’s work.
          </Fact>
          <Fact term="Turning it off">
            Revoke it under Machines: that machine stops mirroring right away,
            and threads it already mirrored stay. Remove the hooks from{" "}
            <Code>{SETTINGS_FILE}</Code> to stop Claude Code sending events too.
          </Fact>
        </dl>
      </SettingsSection>
      <SettingsSection title="What gets mirrored">
        <dl className="divide-y divide-default">
          <Fact term="Shown">
            Each prompt, each tool call with its result, and Claude’s messages,
            streamed live as Claude writes them.
          </Fact>
          <Fact term="Not shown">
            Claude’s thinking and the steps inside subagents. Claude Code
            doesn’t send those to hooks.
          </Fact>
          <Fact term="Pull requests">
            A pull request the session binds in the Claude app or opens with{" "}
            <Code>gh pr create</Code> is linked to the thread.
          </Fact>
          <Fact term="Who sees it">
            Only you and workspace admins. Threads are read-only here: continue
            them in Claude Code.
          </Fact>
          <Fact term="Your sessions">
            Claude Code waits for each hook, so they give up fast: 2 seconds for
            messages, 5 for everything else. A failed one never stops the
            session.
          </Fact>
        </dl>
      </SettingsSection>
    </>
  )
}
