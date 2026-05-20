import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { GithubLogoIcon, KanbanIcon, SlackLogoIcon } from "@phosphor-icons/react";
import type { ComponentType } from "react";

import type { ReposPayload } from "@/lib/api";
import { AppShell, SettingsSection } from "@/components/AppShell";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/integrations")({ component: IntegrationsPage });

type IconType = ComponentType<{ className?: string; weight?: "regular" | "fill" | "duotone" }>;

interface IntegrationRowProps {
  icon: IconType;
  name: string;
  description: string;
  connected: boolean;
  badge?: string;
}

function IntegrationRow({ icon: Icon, name, description, connected, badge }: IntegrationRowProps) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border px-4 py-3 last:border-b-0">
      <div className="flex items-center gap-3">
        <div className="flex size-8 items-center justify-center rounded-md bg-muted">
          <Icon className="size-4" />
        </div>
        <div>
          <div className="flex items-center gap-2 text-xs font-medium">
            {name}
            {badge && (
              <span className="rounded-sm bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
                {badge}
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
      </div>
      <span
        className={cn(
          "rounded-full px-2 py-0.5 text-[10px] font-medium",
          connected
            ? "bg-primary/10 text-primary"
            : "bg-muted text-muted-foreground",
        )}
      >
        {connected ? "Connected" : "Not connected"}
      </span>
    </div>
  );
}

function IntegrationsPage() {
  const session = useSession();
  const repos = useQuery<ReposPayload>({
    queryKey: ["repos"],
    queryFn: async () => {
      try {
        return await api.repos();
      } catch (e) {
        if (e instanceof ApiError && e.status === 401)
          return { installations: [], repositories: [] };
        throw e;
      }
    },
    enabled: !!session.data,
  });

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }
  if (!session.data) return <Navigate to="/login" />;

  const installs = repos.data?.installations ?? [];
  const githubDescription = installs.length
    ? `Connected to ${installs.length} ${installs.length === 1 ? "installation" : "installations"}: ${installs
        .map((i) => i.account ?? "?")
        .join(", ")}`
    : "Install the open-swe GitHub App to enable PR and issue triggers.";

  return (
    <AppShell
      user={session.data}
      title="Integrations"
      description="Connect external tools that can trigger or receive updates from open-swe."
    >
      <SettingsSection title="Source Control">
        <IntegrationRow
          icon={GithubLogoIcon}
          name="GitHub"
          description={githubDescription}
          connected={installs.length > 0}
        />
      </SettingsSection>

      <SettingsSection title="Communication">
        <IntegrationRow
          icon={SlackLogoIcon}
          name="Slack"
          description="Trigger Open SWE from Slack and receive run notifications."
          badge="Team-managed"
          connected
        />
        <IntegrationRow
          icon={KanbanIcon}
          name="Linear"
          description="Delegate Linear issues to Open SWE via mentions or commands."
          badge="Team-managed"
          connected
        />
      </SettingsSection>
    </AppShell>
  );
}
