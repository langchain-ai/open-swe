import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { AccountLinks, LinearLink, LinkProvider, SlackLink } from "@/lib/api";
import { AppHeader } from "@/components/AppHeader";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, providerLinkUrl } from "@/lib/api";
import { useSession } from "@/lib/session";

interface SearchParams {
  source?: LinkProvider;
  user_id?: string;
  linked?: LinkProvider;
}

export const Route = createFileRoute("/account-links")({
  component: AccountLinksPage,
  validateSearch: (search: Record<string, unknown>): SearchParams => ({
    source: search.source === "slack" || search.source === "linear" ? search.source : undefined,
    user_id: typeof search.user_id === "string" ? search.user_id : undefined,
    linked: search.linked === "slack" || search.linked === "linear" ? search.linked : undefined,
  }),
});

function AccountLinksPage() {
  const session = useSession();
  const search = Route.useSearch();
  const qc = useQueryClient();

  const links = useQuery({
    queryKey: ["account-links"],
    queryFn: api.accountLinks,
    enabled: !!session.data,
  });

  const disconnect = useMutation({
    mutationFn: (provider: LinkProvider) => api.deleteAccountLink(provider),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["account-links"] }),
  });

  if (session.isLoading) {
    return (
      <main className="container mx-auto p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }

  if (!session.data) return <Navigate to="/login" />;

  const slack = links.data?.slack ?? null;
  const linear = links.data?.linear ?? null;
  const highlightedProvider = search.linked ?? search.source ?? null;

  return (
    <div className="min-h-svh">
      <AppHeader user={session.data} />
      <main className="container mx-auto p-6">
        <Card className="mx-auto max-w-2xl">
          <CardHeader>
            <CardTitle>Linked accounts</CardTitle>
            <CardDescription>
              Connect your Slack and Linear identities so open-swe knows which GitHub account to
              use when you tag it from those tools.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {links.isLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : (
              <>
                <ProviderRow
                  provider="slack"
                  label="Slack"
                  link={slack}
                  highlighted={highlightedProvider === "slack"}
                  onDisconnect={() => disconnect.mutate("slack")}
                  disconnecting={disconnect.isPending && disconnect.variables === "slack"}
                />
                <ProviderRow
                  provider="linear"
                  label="Linear"
                  link={linear}
                  highlighted={highlightedProvider === "linear"}
                  onDisconnect={() => disconnect.mutate("linear")}
                  disconnecting={disconnect.isPending && disconnect.variables === "linear"}
                />
              </>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  );
}

interface ProviderRowProps {
  provider: LinkProvider;
  label: string;
  link: SlackLink | LinearLink | null;
  highlighted: boolean;
  onDisconnect: () => void;
  disconnecting: boolean;
}

function ProviderRow({
  provider,
  label,
  link,
  highlighted,
  onDisconnect,
  disconnecting,
}: ProviderRowProps) {
  const email = link
    ? "slack_email" in link
      ? link.slack_email
      : link.linear_email
    : "";
  const subtitle = link
    ? email
      ? `Connected as ${email}`
      : "Connected"
    : "Needs authentication";

  return (
    <div
      className={
        "flex items-center justify-between rounded-md border p-4 " +
        (highlighted ? "border-primary" : "")
      }
    >
      <div>
        <div className="font-medium">{label}</div>
        <div className="text-muted-foreground text-sm">{subtitle}</div>
      </div>
      {link ? (
        <Button
          variant="outline"
          size="sm"
          onClick={onDisconnect}
          disabled={disconnecting}
        >
          {disconnecting ? "Disconnecting…" : "Disconnect"}
        </Button>
      ) : (
        <Button asChild size="sm">
          <a href={providerLinkUrl(provider)}>Connect</a>
        </Button>
      )}
    </div>
  );
}

export type { AccountLinks };
