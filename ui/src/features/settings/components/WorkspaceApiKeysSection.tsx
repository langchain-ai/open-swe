import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Input } from "@/components/ui/input"
import { api, type WorkspaceApiKey } from "@/lib/api"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"

export function WorkspaceApiKeysSection({ slug }: { slug: string }) {
  const qc = useQueryClient()
  const queryKey = ["workspace-api-keys", slug]
  const keys = useQuery({
    queryKey,
    queryFn: () => api.listWorkspaceApiKeys(slug),
  })
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [days, setDays] = useState("90")
  const [secret, setSecret] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [revoking, setRevoking] = useState<WorkspaceApiKey | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const validDays =
    Number.isInteger(Number(days)) && Number(days) >= 1 && Number(days) <= 365

  const create = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.createWorkspaceApiKey({
        workspace: slug,
        name: name.trim(),
        description: description || null,
        expires_at: new Date(
          Date.now() + Number(days) * 86400000
        ).toISOString(),
      })
      setSecret(result.secret)
      setCopied(false)
      setName("")
      setDescription("")
      void qc.invalidateQueries({ queryKey })
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create API key")
    } finally {
      setBusy(false)
    }
  }

  const revoke = async () => {
    if (!revoking) return
    setBusy(true)
    setError(null)
    try {
      await api.revokeWorkspaceApiKey(revoking.id)
      setRevoking(null)
      void qc.invalidateQueries({ queryKey })
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not revoke API key")
    } finally {
      setBusy(false)
    }
  }

  return (
    <SettingsSection
      title="API keys"
      description="Workspace-scoped keys for the CLI and automation. Keys can start system threads, not personal threads. Only admins can manage them."
    >
      <div className="space-y-4 px-4 py-3.5">
        {keys.isPending ? (
          <p className="text-sm text-secondary">Loading API keys…</p>
        ) : keys.isError ? (
          <p role="alert" className="text-sm text-error-secondary">
            {keys.error.message}
          </p>
        ) : keys.data.length === 0 ? (
          <p className="text-sm text-secondary">No API keys yet.</p>
        ) : (
          <ul className="divide-y divide-default">
            {keys.data.map((key) => (
              <li
                key={key.id}
                className="flex flex-wrap items-center justify-between gap-3 py-3"
              >
                <div className="space-y-1">
                  <p className="text-sm font-medium">
                    {key.name}{" "}
                    <span className="font-mono text-xs text-secondary">
                      …{key.key_suffix}
                    </span>
                  </p>
                  {key.description && (
                    <p className="text-sm break-words whitespace-pre-wrap text-secondary">
                      {key.description}
                    </p>
                  )}
                  <p className="text-xs text-secondary">
                    Created by{" "}
                    {key.created_by_name || key.created_by || "Unknown user"}
                  </p>
                  <p className="text-xs text-secondary">
                    {key.status} · Expires{" "}
                    {new Date(key.expires_at).toLocaleString()} · Last used{" "}
                    {key.last_used_at
                      ? new Date(key.last_used_at).toLocaleString()
                      : "never"}
                  </p>
                </div>
                {key.status === "active" && (
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={busy}
                    aria-label={`Revoke ${key.name}`}
                    onClick={() => setRevoking(key)}
                  >
                    Revoke<span className="sr-only"> {key.name}</span>
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {secret ? (
          <div className="space-y-3 rounded-md border border-default p-3">
            <p className="text-sm font-medium">
              Copy your key now. It won’t be shown again.
            </p>
            <Input
              aria-label="New API key"
              type="password"
              readOnly
              value={secret}
            />
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={() =>
                  void navigator.clipboard
                    .writeText(secret)
                    .then(() => setCopied(true))
                    .catch(() =>
                      setError(
                        "Could not copy. Select the key and copy it manually."
                      )
                    )
                }
              >
                {copied ? "Copied" : "Copy key"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setSecret(null)}
              >
                I’ve saved my key
              </Button>
            </div>
          </div>
        ) : (
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              if (!busy && name.trim() && validDays) void create()
            }}
          >
            <label className="space-y-1 text-sm">
              Key name
              <Input
                value={name}
                maxLength={120}
                required
                placeholder="Release automation"
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label className="space-y-1 text-sm">
              Expires in (days)
              <Input
                type="number"
                min={1}
                max={365}
                step={1}
                required
                value={days}
                onChange={(event) => setDays(event.target.value)}
              />
            </label>
            <label className="w-full space-y-1 text-sm">
              Description (optional)
              <Textarea
                value={description}
                maxLength={4000}
                placeholder="What is this key used for?"
                onChange={(event) => setDescription(event.target.value)}
              />
            </label>
            <Button
              type="submit"
              size="sm"
              disabled={busy || !name.trim() || !validDays}
            >
              {busy ? "Creating…" : "Create API key"}
            </Button>
          </form>
        )}
        {error && (
          <p role="alert" className="text-sm text-error-secondary">
            {error}
          </p>
        )}
      </div>
      <AlertDialog
        open={!!revoking}
        onOpenChange={(open) => {
          if (!open && !busy) setRevoking(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke {revoking?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Any automation using this key will immediately lose access. This
              cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {error && (
            <p role="alert" className="text-sm text-error-secondary">
              {error}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={busy}
              onClick={(event) => {
                event.preventDefault()
                void revoke()
              }}
            >
              {busy ? "Revoking…" : "Revoke key"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SettingsSection>
  )
}
