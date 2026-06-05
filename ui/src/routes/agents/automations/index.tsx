import { createFileRoute } from "@tanstack/react-router";

import { AutomationsList } from "@/components/agents/AutomationsList";
import { AgentsShell } from "@/components/agents/AgentsSidebar";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/agents/automations/")({
  component: AutomationsIndexPage,
});

function AutomationsIndexPage() {
  const session = useSession();
  if (!session.data) return null;

  return (
    <AgentsShell user={session.data}>
      <AutomationsList />
    </AgentsShell>
  );
}
