import { create } from "zustand";
import type {
  DesignSessionState,
  FeatureChangeProposal,
  ClarifyingQuestion,
} from "@openswe/shared/open-swe/design/types";
import { FeatureGraph } from "@openswe/shared/feature-graph/graph";
import type { FeatureNode } from "@openswe/shared/feature-graph/types";
import { coerceFeatureGraph } from "@/lib/coerce-feature-graph";

export type DesignThreadStatus =
  | "idle"
  | "creating"
  | "active"
  | "handing_off"
  | "error";

export type HandoffStatus =
  | "idle"
  | "pending"
  | "success"
  | "error";

interface HandoffResult {
  plannerThreadId: string;
  runId: string;
  featureIds: string[];
  timestamp: number;
}

interface DesignThreadStoreState {
  // Thread identification
  designThreadId: string | null;
  managerThreadId: string | null;

  // Thread status
  status: DesignThreadStatus;
  error: string | null;

  // Feature graph state
  featureGraph: FeatureGraph | null;
  features: FeatureNode[];
  readyFeatureIds: string[];

  // Design session state
  designSession: DesignSessionState | null;
  pendingProposals: FeatureChangeProposal[];
  clarifyingQuestions: ClarifyingQuestion[];
  changeHistory: Array<{
    proposalId: string;
    action: string;
    timestamp: string;
    summary: string;
  }>;

  // Handoff state
  handoffStatus: HandoffStatus;
  handoffError: string | null;
  lastHandoff: HandoffResult | null;

  // Loading states
  isLoading: boolean;
  isCreating: boolean;
  isHandingOff: boolean;

  // Actions
  createDesignThread: (options?: {
    managerThreadId?: string;
    initialPrompt?: string;
  }) => Promise<string>;
  fetchDesignState: (threadId: string) => Promise<void>;
  handoffToPlanner: (options?: {
    featureIds?: string[];
  }) => Promise<HandoffResult>;
  setDesignThreadId: (threadId: string | null) => void;
  setReadyFeatureIds: (featureIds: string[]) => void;
  markFeatureReady: (featureId: string) => void;
  unmarkFeatureReady: (featureId: string) => void;
  reset: () => void;
}

const INITIAL_STATE: Omit<
  DesignThreadStoreState,
  | "createDesignThread"
  | "fetchDesignState"
  | "handoffToPlanner"
  | "setDesignThreadId"
  | "setReadyFeatureIds"
  | "markFeatureReady"
  | "unmarkFeatureReady"
  | "reset"
> = {
  designThreadId: null,
  managerThreadId: null,
  status: "idle",
  error: null,
  featureGraph: null,
  features: [],
  readyFeatureIds: [],
  designSession: null,
  pendingProposals: [],
  clarifyingQuestions: [],
  changeHistory: [],
  handoffStatus: "idle",
  handoffError: null,
  lastHandoff: null,
  isLoading: false,
  isCreating: false,
  isHandingOff: false,
};

export const useDesignThreadStore = create<DesignThreadStoreState>(
  (set, get) => ({
    ...INITIAL_STATE,

    async createDesignThread(options) {
      const { isCreating } = get();
      if (isCreating) {
        throw new Error("Design thread creation already in progress");
      }

      set({
        isCreating: true,
        status: "creating",
        error: null,
      });

      try {
        const response = await fetch("/api/design/create", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            manager_thread_id: options?.managerThreadId,
            initial_prompt: options?.initialPrompt,
          }),
        });

        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.error ?? "Failed to create design thread");
        }

        const data = await response.json();
        const designThreadId = data.design_thread_id;

        set({
          designThreadId,
          managerThreadId: data.manager_thread_id ?? options?.managerThreadId ?? null,
          status: "active",
          isCreating: false,
          error: null,
        });

        // Fetch initial state
        await get().fetchDesignState(designThreadId);

        return designThreadId;
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to create design thread";
        set({
          isCreating: false,
          status: "error",
          error: message,
        });
        throw new Error(message);
      }
    },

    async fetchDesignState(threadId) {
      if (!threadId) return;

      set({ isLoading: true });

      try {
        const response = await fetch(`/api/design/state?thread_id=${encodeURIComponent(threadId)}`);

        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.error ?? "Failed to fetch design state");
        }

        const data = await response.json();
        const featureGraph = coerceFeatureGraph(data.feature_graph);
        const features = featureGraph?.listFeatures() ?? [];

        set({
          designThreadId: threadId,
          managerThreadId: data.manager_thread_id ?? null,
          featureGraph,
          features,
          readyFeatureIds: data.ready_feature_ids ?? [],
          designSession: data.design_session ?? null,
          pendingProposals: data.pending_proposals ?? [],
          clarifyingQuestions: data.clarifying_questions ?? [],
          changeHistory: data.change_history ?? [],
          status: "active",
          isLoading: false,
          error: null,
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to fetch design state";
        set({
          isLoading: false,
          error: message,
        });
      }
    },

    async handoffToPlanner(options) {
      const { designThreadId, isHandingOff, readyFeatureIds } = get();

      if (!designThreadId) {
        throw new Error("No design thread active");
      }

      if (isHandingOff) {
        throw new Error("Handoff already in progress");
      }

      const featureIds = options?.featureIds ?? readyFeatureIds;

      if (featureIds.length === 0) {
        throw new Error("No features selected for handoff");
      }

      set({
        isHandingOff: true,
        handoffStatus: "pending",
        handoffError: null,
      });

      try {
        const response = await fetch("/api/design/handoff", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            design_thread_id: designThreadId,
            feature_ids: featureIds,
          }),
        });

        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.error ?? "Failed to hand off to planner");
        }

        const data = await response.json();

        const result: HandoffResult = {
          plannerThreadId: data.planner_thread_id,
          runId: data.run_id,
          featureIds: data.feature_ids,
          timestamp: Date.now(),
        };

        set({
          isHandingOff: false,
          handoffStatus: "success",
          handoffError: null,
          lastHandoff: result,
        });

        return result;
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to hand off to planner";
        set({
          isHandingOff: false,
          handoffStatus: "error",
          handoffError: message,
        });
        throw new Error(message);
      }
    },

    setDesignThreadId(threadId) {
      if (threadId) {
        set({ designThreadId: threadId, status: "active" });
        get().fetchDesignState(threadId);
      } else {
        set({ ...INITIAL_STATE });
      }
    },

    setReadyFeatureIds(featureIds) {
      set({ readyFeatureIds: featureIds });
    },

    markFeatureReady(featureId) {
      const { readyFeatureIds, featureGraph } = get();
      if (!featureGraph?.hasFeature(featureId)) return;
      if (readyFeatureIds.includes(featureId)) return;

      set({ readyFeatureIds: [...readyFeatureIds, featureId] });
    },

    unmarkFeatureReady(featureId) {
      const { readyFeatureIds } = get();
      set({ readyFeatureIds: readyFeatureIds.filter((id) => id !== featureId) });
    },

    reset() {
      set({ ...INITIAL_STATE });
    },
  }),
);
