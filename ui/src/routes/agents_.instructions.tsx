import { Navigate, createFileRoute } from "@tanstack/react-router";

import { AgentInstructionsPanel } from "@/components/AgentInstructionsPanel";
import { AppShell } from "@/components/AppShell";
import { Skeleton } from "@/components/ui/skeleton";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/agents_/instructions")({
  component: AgentInstructionsPage,
});

function AgentInstructionsPage() {
  const session = useSession();

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }
  if (!session.data) return <Navigate to="/login" />;

  return (
    <AppShell
      user={session.data}
      title="Repository Instructions"
      description="Per-repo custom instructions appended to the coding agent's system prompt for runs targeting that repository."
      backTo={{ to: "/cloud-agents", label: "Back to Open SWE Agent" }}
    >
      <div className="rounded-lg border border-border bg-card">
        <AgentInstructionsPanel />
      </div>
    </AppShell>
  );
}
