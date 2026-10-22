import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"

import type { ClaudeCodeSyncToken, MintedClaudeCodeSyncToken } from "@/lib/api"
import { SettingsSection } from "@/components/AppShell"
import { api } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { ConfirmDialog } from "./ConfirmDialog"

const TOKENS_KEY = ["claudeCodeSyncTokens"]

function MintedToken({
  token,
  onDone,
}: {
  token: MintedClaudeCodeSyncToken
  onDone: () => void
}) {
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const settings = JSON.stringify(token.settings, null, 2)
  return (
    <div className="space-y-space-3 rounded-md border border-default p-space-3">
      <p className="text-sm font-medium text-primary">
        Merge this into <code>~/.claude/settings.json</code>. It holds your
        token, which won’t be shown again.
      </p>
      <pre className="max-h-64 overflow-auto rounded-md bg-surface-level-2 p-space-3 font-mono text-xs text-primary">
        {settings}
      </pre>
      <div className="flex gap-space-2">
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
        <Button size="xs" color="secondary" variant="outlined" onClick={onDone}>
          I’ve saved them
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-error-secondary">
          {error}
        </p>
      )}
    </div>
  )
}

/** Tokens that let Claude Code mirror this person's sessions as read-only threads. */
export function ClaudeCodeSyncSection() {
  const qc = useQueryClient()
  const tokens = useQuery({
    queryKey: TOKENS_KEY,
    queryFn: api.listClaudeCodeSyncTokens,
  })
  const [name, setName] = useState("")
  const [minted, setMinted] = useState<MintedClaudeCodeSyncToken | null>(null)
  const [revoking, setRevoking] = useState<ClaudeCodeSyncToken | null>(null)

  const create = useMutation({
    meta: { errorTitle: "Couldn't create a Claude Code token" },
    mutationFn: (tokenName: string) => api.createClaudeCodeSyncToken(tokenName),
    onSuccess: (token) => {
      setMinted(token)
      setName("")
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: TOKENS_KEY }),
  })
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
      title="Claude Code"
      description="Mirror your Claude Code sessions here as read-only threads that update while they run. Each machine gets its own token."
    >
      <div className="space-y-space-4 px-space-4 py-space-3">
        {tokens.isPending ? (
          <p className="text-sm text-secondary">Loading tokens…</p>
        ) : tokens.isError ? (
          <p role="alert" className="text-sm text-error-secondary">
            {tokens.error.message}
          </p>
        ) : tokens.data.length === 0 ? (
          <p className="text-sm text-secondary">
            No machines are mirroring yet.
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
                    Created {new Date(token.created_at).toLocaleString()} · Last
                    used{" "}
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
        {minted ? (
          <MintedToken token={minted} onDone={() => setMinted(null)} />
        ) : (
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
