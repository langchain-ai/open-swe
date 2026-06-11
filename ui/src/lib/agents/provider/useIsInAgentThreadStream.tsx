import { createContext, useContext } from "react";
import type { ReactNode } from "react";

/**
 * Lightweight marker for whether the current subtree is rendering inside an
 * *active* thread (the `/agents/$threadId` route), as opposed to elsewhere
 * under the shared `/agents` stream provider (e.g. `AgentsHome`, automations).
 *
 * The `AgentThreadStreamProvider` now spans the whole `/agents` layout, so
 * `useStreamContext()` is callable everywhere underneath it — but shared UI
 * such as `CloudPromptBar` must still distinguish "in a live thread" (show the
 * stop button, mount nested subagent activity) from the home prompt. The
 * boundary is wrapped only around the thread view, so this stays `false` on
 * the home page where there is no thread to act on.
 */
const AgentThreadStreamBoundaryContext = createContext(false);

export function useIsInAgentThreadStream(): boolean {
  return useContext(AgentThreadStreamBoundaryContext);
}

export function AgentThreadStreamBoundary({ children }: { children: ReactNode }) {
  return (
    <AgentThreadStreamBoundaryContext.Provider value={true}>
      {children}
    </AgentThreadStreamBoundaryContext.Provider>
  );
}
