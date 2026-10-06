import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ConfirmableAction } from "@langchain/gtm-platform-design-system/patterns/confirmable-action"
import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import {
  FormField,
  FormSection,
} from "@langchain/gtm-platform-design-system/patterns/form-field"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import {
  Alert,
  AlertTitle,
} from "@langchain/gtm-platform-design-system/ui/alert"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"
import { AlertTriangle, Key } from "@/components/glyphs"
import { api, type WorkspaceApiKey } from "@/lib/api"

import { FormError } from "./WorkspaceSandboxSection"

function ApiKeyRow({
  apiKey,
  onRevoke,
}: {
  apiKey: WorkspaceApiKey
  onRevoke: (key: WorkspaceApiKey) => Promise<void>
}) {
  return (
    <Inline
      render={<li />}
      gap="md"
      align="center"
      justify="between"
      wrap
      className="border-b border-line px-5 py-3 last:border-b-0"
    >
      <Stack gap="xs" className="min-w-0 flex-1">
        <Box render={<p />} className="text-label font-medium text-ink">
          {apiKey.name}{" "}
          <Box
            render={<span />}
            className="font-mono text-meta text-ink-subtle"
          >
            …{apiKey.key_suffix}
          </Box>
        </Box>
        {apiKey.description && (
          <Box
            render={<p />}
            className="text-label break-words whitespace-pre-wrap text-ink-muted"
          >
            {apiKey.description}
          </Box>
        )}
        <Box render={<p />} className="text-meta text-ink-subtle">
          Created by{" "}
          {apiKey.created_by_name || apiKey.created_by || "Unknown user"}
        </Box>
        <Box render={<p />} className="text-meta text-ink-subtle">
          {apiKey.status} · Expires{" "}
          {new Date(apiKey.expires_at).toLocaleString()} · Last used{" "}
          {apiKey.last_used_at
            ? new Date(apiKey.last_used_at).toLocaleString()
            : "never"}
        </Box>
      </Stack>
      {apiKey.status === "active" && (
        <ConfirmableAction
          trigger={
            <Button
              size="compact"
              variant="outline"
              aria-label={`Revoke ${apiKey.name}`}
            >
              Revoke<span className="sr-only"> {apiKey.name}</span>
            </Button>
          }
          title={`Revoke ${apiKey.name}?`}
          description="Any automation using this key will immediately lose access. This cannot be undone."
          confirmLabel="Revoke key"
          onConfirm={() => onRevoke(apiKey)}
        />
      )}
    </Inline>
  )
}

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

  // The dialog states a rejection itself, so this only has to throw.
  const revoke = async (key: WorkspaceApiKey) => {
    await api.revokeWorkspaceApiKey(key.id)
    void qc.invalidateQueries({ queryKey })
  }

  return (
    <PageSection
      contained
      title="API keys"
      description="Workspace-scoped keys for the CLI and automation. Keys can start system threads, not personal threads. Only admins can manage them."
    >
      <Stack gap="none">
        {keys.isPending ? (
          <Box padding="lg">
            <Skeleton className="h-row-record w-full" />
          </Box>
        ) : keys.isError ? (
          <Box padding="lg">
            <StateNotice
              tone="RISK"
              icon={AlertTriangle}
              title="API keys did not load"
              description={keys.error.message}
            />
          </Box>
        ) : keys.data.length === 0 ? (
          <EmptyState
            icon={Key}
            title="No API keys yet."
            description="Create one below for the CLI or an automation."
          />
        ) : (
          <Stack render={<ul />} gap="none">
            {keys.data.map((key) => (
              <ApiKeyRow key={key.id} apiKey={key} onRevoke={revoke} />
            ))}
          </Stack>
        )}
        <Stack gap="lg" className="border-t border-line px-5 py-4">
          {secret ? (
            <Stack gap="md">
              <Alert tone="attention" icon={Key}>
                <AlertTitle>
                  Copy your key now. It won’t be shown again.
                </AlertTitle>
              </Alert>
              <FormField
                label="New API key"
                control={<Input type="password" readOnly value={secret} />}
              />
              <Inline gap="sm">
                <Button
                  size="compact"
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
                  size="compact"
                  variant="outline"
                  onClick={() => setSecret(null)}
                >
                  I’ve saved my key
                </Button>
              </Inline>
            </Stack>
          ) : (
            <Stack
              render={
                <form
                  onSubmit={(event) => {
                    event.preventDefault()
                    if (!busy && name.trim() && validDays) void create()
                  }}
                />
              }
              gap="lg"
            >
              <FormSection title="Create a key">
                <Box className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <FormField
                    label="Key name"
                    required
                    control={
                      <Input
                        value={name}
                        maxLength={120}
                        placeholder="Release automation"
                        onChange={(event) => setName(event.target.value)}
                      />
                    }
                  />
                  <FormField
                    label="Expires in (days)"
                    required
                    control={
                      <Input
                        type="number"
                        min={1}
                        max={365}
                        step={1}
                        value={days}
                        onChange={(event) => setDays(event.target.value)}
                      />
                    }
                  />
                </Box>
                <FormField
                  label="Description (optional)"
                  control={
                    <Textarea
                      value={description}
                      maxLength={4000}
                      placeholder="What is this key used for?"
                      onChange={(event) => setDescription(event.target.value)}
                    />
                  }
                />
              </FormSection>
              <Inline gap="sm" justify="end">
                <Button
                  type="submit"
                  size="compact"
                  disabled={busy || !name.trim() || !validDays}
                >
                  {busy ? "Creating…" : "Create API key"}
                </Button>
              </Inline>
            </Stack>
          )}
          {error && <FormError message={error} />}
        </Stack>
      </Stack>
    </PageSection>
  )
}
