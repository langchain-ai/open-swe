import { createContext, useContext, type ReactNode } from "react";

/**
 * Lightweight marker for whether the current subtree is wrapped by
 * {@link AgentThreadStreamProvider}.
 *
 * `useStreamContext()` throws when called outside `StreamProvider`. Shared UI
 * such as `CloudPromptBar` is also rendered on pages without an active thread
 * stream (e.g. `AgentsHome`), so components that need stream primitives must
 * not call that hook unconditionally.
 *
 * Mount stream-dependent children (e.g. the run stop button) only when this
 * hook returns `true`, so the hook runs exclusively inside a provider tree.
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
