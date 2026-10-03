import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type DocsSettings } from "@/lib/api"

import { useSession } from "@/lib/session"

const settingsKey = ["docsSettings"] as const

export function DocsTargetSection() {
  const session = useSession()
  const settings = useQuery({
    queryKey: settingsKey,
    queryFn: api.getDocsSettings,
    enabled: Boolean(session.data?.is_admin),
  })
  return (
    <SettingsSection
      title="Documentation target"
      description="Choose where documentation updates are authored. Enable docs checks independently from code review in each repository's settings. Docs checks run for open, non-draft PRs without skip-docs."
    >
      {!session.data?.is_admin ? (
        <p className="p-4 text-sm text-muted-foreground">
          Ask an administrator to configure the documentation target.
        </p>
      ) : settings.isLoading ? (
        <Skeleton className="m-4 h-64" />
      ) : settings.isError ? (
        <p role="alert" className="p-4 text-sm text-destructive">
          Could not load docs settings. {settings.error.message}
        </p>
      ) : settings.data ? (
        <DocsForm key={settings.data.revision} initial={settings.data} />
      ) : null}
    </SettingsSection>
  )
}

function DocsForm({ initial }: { initial: DocsSettings }) {
  const [draft, setDraft] = useState(initial)
  const client = useQueryClient()
  const save = useMutation({
    mutationFn: api.saveDocsSettings,
    meta: { errorTitle: "Could not save documentation target" },
    onMutate: async (next) => {
      await client.cancelQueries({ queryKey: settingsKey })
      const previous = client.getQueryData<DocsSettings>(settingsKey)
      client.setQueryData(settingsKey, next)
      return { previous }
    },
    onError: (_error, _next, context) => {
      if (context?.previous) client.setQueryData(settingsKey, context.previous)
    },
    onSuccess: (saved) => {
      client.setQueryData(settingsKey, saved)
      void client.invalidateQueries({ queryKey: ["autoDocsRepos"] })
    },
  })
  return (
    <form
      className="space-y-5 p-4"
      onSubmit={(event) => {
        event.preventDefault()
        save.mutate({
          ...draft,
          enabled: Boolean(draft.docs_repository),
        })
      }}
    >
      <div className="space-y-2">
        <Label htmlFor="docs-repository">Docs repository</Label>
        <Input
          id="docs-repository"
          placeholder="owner/docs"
          value={draft.docs_repository}
          required
          onChange={(event) =>
            setDraft({ ...draft, docs_repository: event.target.value })
          }
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="docs-base">Docs base branch</Label>
        <Input
          id="docs-base"
          value={draft.docs_base_branch}
          required
          onChange={(event) =>
            setDraft({ ...draft, docs_base_branch: event.target.value })
          }
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="docs-mcp">Docs MCP URL (optional)</Label>
        <Input
          id="docs-mcp"
          type="url"
          placeholder="https://docs.example.com/mcp"
          value={draft.docs_mcp_url}
          onChange={(event) =>
            setDraft({ ...draft, docs_mcp_url: event.target.value })
          }
        />
        <p className="text-xs text-muted-foreground">
          Connect read-only search and fetch tools for your published
          documentation.
        </p>
      </div>
      <p className="text-xs text-muted-foreground">
        Linked docs PRs receive review comments only. Without a link, the agent
        creates a draft docs PR when needed and links it in the source PR
        comments.
      </p>
      <Button type="submit" disabled={save.isPending}>
        {save.isPending ? "Saving…" : "Save settings"}
      </Button>
    </form>
  )
}
