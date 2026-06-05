import { createFileRoute } from "@tanstack/react-router";

import { AutomationEditor } from "@/components/agents/AutomationEditor";
import { AgentsShell } from "@/components/agents/AgentsSidebar";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/agents/automations/new")({
  component: NewAutomationPage,
});

function NewAutomationPage() {
  const session = useSession();
  if (!session.data) return null;

  return (
    <AgentsShell user={session.data}>
      <AutomationEditor mode="create" />
    </AgentsShell>
  );
}
