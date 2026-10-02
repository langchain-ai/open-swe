import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { AppShell, SettingsSection } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type DocsSettings } from "@/lib/api";
import { RequireLogin } from "@/lib/auth-redirect";
import { pageTitle } from "@/lib/pageTitle";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/docs")({
  component: DocsPage,
  head: () => ({ meta: [{ title: pageTitle("Open SWE Docs") }] }),
});
const settingsKey = ["docsSettings"] as const;

function DocsPage() {
  const session = useSession();
  const settings = useQuery({
    queryKey: settingsKey,
    queryFn: api.getDocsSettings,
    enabled: Boolean(session.data?.is_admin),
  });
  if (session.isLoading)
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    );
  if (!session.data) return <RequireLogin />;
  return (
    <AppShell
      user={session.data}
      title="Open SWE Docs"
      description="Check documentation against pull request changes and draft updates in your docs repository."
    >
      <SettingsSection
        title="Documentation agent"
        description="Runs for open, non-draft PRs. Add skip-docs to skip a run. Link any docs PR URL in a source PR description or comment to review it instead of creating a new PR."
      >
        {!session.data.is_admin ? (
          <p className="p-4 text-sm text-muted-foreground">
            Ask an administrator to configure Open SWE Docs.
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
    </AppShell>
  );
}

function DocsForm({ initial }: { initial: DocsSettings }) {
  const [draft, setDraft] = useState(initial);
  const [sources, setSources] = useState(
    initial.source_repositories.join("\n"),
  );
  const client = useQueryClient();
  const save = useMutation({
    mutationFn: api.saveDocsSettings,
    onMutate: async (next) => {
      await client.cancelQueries({ queryKey: settingsKey });
      const previous = client.getQueryData<DocsSettings>(settingsKey);
      client.setQueryData(settingsKey, next);
      return { previous };
    },
    onError: (error, _next, context) => {
      if (context?.previous) client.setQueryData(settingsKey, context.previous);
      toast.error(error.message || "Could not save docs settings");
    },
    onSuccess: (saved) => {
      client.setQueryData(settingsKey, saved);
      toast.success("Docs settings saved");
    },
  });
  return (
    <form
      className="space-y-5 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate({
          ...draft,
          source_repositories: sources.split(/[\s,]+/).filter(Boolean),
        });
      }}
    >
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={draft.enabled}
          onChange={(event) =>
            setDraft({ ...draft, enabled: event.target.checked })
          }
        />
        Enable Open SWE Docs
      </label>
      <div className="space-y-2">
        <Label htmlFor="docs-repository">Docs repository</Label>
        <Input
          id="docs-repository"
          placeholder="owner/docs"
          value={draft.docs_repository}
          required={draft.enabled}
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
      <div className="space-y-2">
        <Label htmlFor="docs-sources">Source repositories</Label>
        <textarea
          id="docs-sources"
          className="min-h-28 w-full rounded-md border border-input bg-transparent p-3 text-sm"
          placeholder={"owner/application\nowner/sdk"}
          value={sources}
          onChange={(event) => setSources(event.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          One owner/repository per line. The GitHub App needs access to each
          source and write access to the docs repository.
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
  );
}
