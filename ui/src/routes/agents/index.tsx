import { createFileRoute } from "@tanstack/react-router";

import { AgentsHome } from "@/components/agents/AgentsHome";
import { AgentsShell } from "@/components/agents/AgentsSidebar";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/agents/")({
  component: AgentsIndexPage,
});

function AgentsIndexPage() {
  const session = useSession();
  if (!session.data) return null;

  return (
    <AgentsShell user={session.data}>
      <AgentsHome />
    </AgentsShell>
  );
}
