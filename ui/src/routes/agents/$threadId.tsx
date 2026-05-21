import { Navigate, createFileRoute } from "@tanstack/react-router";

import { AgentThreadView } from "@/components/agents/AgentThreadView";
import { getThread } from "@/lib/agents/mock-data";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/agents/$threadId")({
  component: AgentThreadPage,
});

function AgentThreadPage() {
  const { threadId } = Route.useParams();
  const session = useSession();
  const thread = getThread(threadId);

  if (session.isLoading) return null;
  if (!session.data) return <Navigate to="/login" />;
  if (!thread) return <Navigate to="/agents" />;

  return <AgentThreadView user={session.data} thread={thread} />;
}
