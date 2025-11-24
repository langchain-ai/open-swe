import { create } from "zustand";

import { FeatureGraph } from "@openswe/shared/feature-graph/graph";
import { testsForFeature } from "@openswe/shared/feature-graph/mappings";
import type {
  ArtifactCollection,
  ArtifactRef,
  FeatureNode,
} from "@openswe/shared/feature-graph/types";
import type { FeatureProposal } from "@openswe/shared/open-swe/manager/types";

import {
  FeatureGraphFetchResult,
  mapFeatureProposalState,
  mapFeatureGraphPayload,
  normalizeFeatureIds,
} from "@/lib/feature-graph-payload";
import {
  fetchFeatureGraph,
  performFeatureProposalAction,
  type FeatureProposalAction,
  requestFeatureGraphGeneration,
  startFeatureDevelopmentRun,
} from "@/services/feature-graph.service";

export type FeatureResource = {
  id: string;
  label: string;
  secondaryLabel?: string;
  description?: string;
  href?: string;
};

export type FeatureRunStatus =
  | "idle"
  | "starting"
  | "running"
  | "completed"
  | "error";

export type FeatureRunState = {
  threadId: string | null;
  runId: string | null;
  status: FeatureRunStatus;
  error?: string | null;
  updatedAt: number;
};

type ProposalActionState = {
  status: "idle" | "pending" | "error";
  error?: string | null;
  message?: string | null;
  updatedAt: number;
};

interface FeatureGraphStoreState {
  threadId: string | null;
  graph: FeatureGraph | null;
  features: FeatureNode[];
  featuresById: Record<string, FeatureNode>;
  activeFeatureIds: string[];
  proposals: FeatureProposal[];
  activeProposalId: string | null;
  proposalActions: Record<string, ProposalActionState>;
  selectedFeatureId: string | null;
  testsByFeatureId: Record<string, FeatureResource[]>;
  artifactsByFeatureId: Record<string, FeatureResource[]>;
  featureRuns: Record<string, FeatureRunState>;
  isLoading: boolean;
  isGeneratingGraph: boolean;
  error: string | null;
  fetchGraphForThread: (
    threadId: string,
    options?: { force?: boolean },
  ) => Promise<void>;
  generateGraph: (threadId: string, prompt: string) => Promise<void>;
  requestGraphGeneration: (threadId: string) => Promise<void>;
  startFeatureDevelopment: (featureId: string) => Promise<void>;
  respondToProposal: (
    proposalId: string,
    action: FeatureProposalAction,
    options?: { rationale?: string },
  ) => Promise<string | void>;
  setFeatureRunStatus: (
    featureId: string,
    status: FeatureRunStatus,
    options?: {
      runId?: string | null;
      threadId?: string | null;
      error?: string;
    },
  ) => void;
  selectFeature: (featureId: string | null) => void;
  setActiveFeatureIds: (featureIds?: string[] | null) => void;
  clear: () => void;
}

const INITIAL_STATE: Omit<
    FeatureGraphStoreState,
    | "fetchGraphForThread"
    | "generateGraph"
    | "requestGraphGeneration"
    | "startFeatureDevelopment"
    | "respondToProposal"
    | "setFeatureRunStatus"
    | "selectFeature"
    | "setActiveFeatureIds"
    | "clear"
  > = {
  threadId: null,
  graph: null,
  features: [],
  featuresById: {},
  activeFeatureIds: [],
  proposals: [],
  activeProposalId: null,
  proposalActions: {},
  selectedFeatureId: null,
  testsByFeatureId: {},
  artifactsByFeatureId: {},
  featureRuns: {},
  isLoading: false,
  isGeneratingGraph: false,
  error: null,
};

export const useFeatureGraphStore = create<FeatureGraphStoreState>(
  (set, get) => ({
    ...INITIAL_STATE,
    async fetchGraphForThread(threadId, options) {
      const { threadId: currentThreadId, isLoading, graph } = get();
      const shouldSkip =
        !threadId ||
        (!options?.force &&
          threadId === currentThreadId &&
          (graph !== null || isLoading));

      if (shouldSkip) return;

      set((state) => ({
        ...INITIAL_STATE,
        threadId,
        isLoading: true,
        isGeneratingGraph: state.isGeneratingGraph,
      }));

      try {
        const result = await fetchFeatureGraph(threadId);
        set((state) => mapFetchResultToState(state, result));
      } catch (error) {
        const message =
          error instanceof Error
            ? error.message
            : "Failed to load feature graph";
        set({
          threadId,
          isLoading: false,
          isGeneratingGraph: false,
          error: message,
          graph: null,
          features: [],
          featuresById: {},
          testsByFeatureId: {},
          artifactsByFeatureId: {},
          activeFeatureIds: [],
          proposals: [],
          activeProposalId: null,
          proposalActions: {},
          selectedFeatureId: null,
        });
      }
    },
    async generateGraph(threadId, prompt) {
      const { isGeneratingGraph } = get();
      if (!threadId || isGeneratingGraph) return;

      set((state) => ({
        ...state,
        threadId,
        isGeneratingGraph: true,
        isLoading: true,
        error: null,
      }));

      try {
        const response = await fetch("/api/feature-graph/generate", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ thread_id: threadId, prompt }),
        });

        if (!response.ok) {
          const payload = await response.json().catch(() => null);
          const message =
            (payload && typeof payload.message === "string"
              ? payload.message
              : null) ?? "Failed to generate feature graph";
          throw new Error(message);
        }

        const payload = await response.json();
        const result = mapFeatureGraphPayload(payload);

        set((state) =>
          mapFetchResultToState(
            { ...state, threadId, isGeneratingGraph: false },
            result,
          ),
        );
      } catch (error) {
        const message =
          error instanceof Error
            ? error.message
            : "Failed to generate feature graph";
        set((state) => ({
          ...state,
          threadId,
          isGeneratingGraph: false,
          isLoading: false,
          error: message,
        }));
      }
    },
    async requestGraphGeneration(threadId) {
      const { isGeneratingGraph } = get();
      if (!threadId || isGeneratingGraph) return;

      set({ isGeneratingGraph: true, threadId, error: null });

      try {
        await requestFeatureGraphGeneration(threadId);
        await get().fetchGraphForThread(threadId, { force: true });
      } catch (error) {
        const message =
          error instanceof Error
            ? error.message
            : "Failed to request feature graph generation";
        set({
          threadId,
          isGeneratingGraph: false,
          error: message,
        });
      }
    },
    async startFeatureDevelopment(featureId) {
      const { threadId, featureRuns, featuresById } = get();
      if (!threadId || !featureId || !featuresById[featureId]) return;

      const existingRun = featureRuns[featureId];
      if (
        existingRun?.status === "running" ||
        existingRun?.status === "starting"
      ) {
        set({ selectedFeatureId: featureId });
        return;
      }

      const nextRunState: FeatureRunState = {
        threadId: null,
        runId: null,
        status: "starting",
        error: null,
        updatedAt: Date.now(),
      };

      set((state) => ({
        ...state,
        selectedFeatureId: featureId,
        featureRuns: {
          ...state.featureRuns,
          [featureId]: nextRunState,
        },
      }));

      try {
        const { plannerThreadId, runId } = await startFeatureDevelopmentRun(
          threadId,
          featureId,
        );

        set((state) => ({
          ...state,
          selectedFeatureId: featureId,
          featureRuns: {
            ...state.featureRuns,
            [featureId]: {
              threadId: plannerThreadId,
              runId,
              status: "running",
              error: null,
              updatedAt: Date.now(),
            },
          },
        }));
      } catch (error) {
        const message =
          error instanceof Error
            ? error.message
            : "Failed to start feature development";

        set((state) => ({
          ...state,
          selectedFeatureId: featureId,
          featureRuns: {
            ...state.featureRuns,
            [featureId]: {
              threadId: null,
              runId: null,
              status: "error",
              error: message,
              updatedAt: Date.now(),
            },
          },
        }));

        throw new Error(message);
      }
    },
    async respondToProposal(proposalId, action, options) {
      const { threadId, proposals, proposalActions } = get();
      if (!threadId || !proposalId) return;

      const target = proposals.find(
        (proposal) => proposal.proposalId === proposalId,
      );

      if (!target) return;

      set({
        proposalActions: {
          ...proposalActions,
          [proposalId]: {
            status: "pending",
            error: null,
            message: null,
            updatedAt: Date.now(),
          },
        },
      });

      try {
        const result = await performFeatureProposalAction({
          threadId,
          proposalId,
          featureId: target.featureId,
          action,
          rationale: options?.rationale,
        });

        set((state) => {
          const nextState = mapFetchResultToState(state, result);
          return {
            ...nextState,
            proposalActions: {
              ...nextState.proposalActions,
              [proposalId]: {
                status: "idle",
                error: null,
                message: result.message,
                updatedAt: Date.now(),
              },
            },
          };
        });

        return result.message ?? undefined;
      } catch (error) {
        const message =
          error instanceof Error
            ? error.message
            : "Failed to process proposal action";

        set((state) => ({
          ...state,
          proposalActions: {
            ...state.proposalActions,
            [proposalId]: {
              status: "error",
              error: message,
              updatedAt: Date.now(),
            },
          },
        }));
        throw new Error(message);
      }
    },
    setFeatureRunStatus(featureId, status, options) {
      if (!featureId) return;

      set((state) => {
        const current = state.featureRuns[featureId];

        return {
          ...state,
          featureRuns: {
            ...state.featureRuns,
            [featureId]: {
              threadId: options?.threadId ?? current?.threadId ?? null,
              runId: options?.runId ?? current?.runId ?? null,
              status,
              error: options?.error ?? null,
              updatedAt: Date.now(),
            },
          },
        };
      });
    },
    selectFeature(featureId) {
      if (!featureId) {
        set({ selectedFeatureId: null });
        return;
      }

      const { featuresById } = get();
      if (!featuresById[featureId]) return;

      set({ selectedFeatureId: featureId });
    },
    setActiveFeatureIds(featureIds) {
      const normalized = normalizeFeatureIds(featureIds);
      const state = get();

      const hasActiveFeatureIdsChanged =
        normalized.length !== state.activeFeatureIds.length ||
        normalized.some((id, index) => id !== state.activeFeatureIds[index]);

      if (normalized.length === 0) {
        if (!hasActiveFeatureIdsChanged) {
          return;
        }

        set({
          activeFeatureIds: [],
          selectedFeatureId: state.selectedFeatureId,
        });
        return;
      }

      const currentSelection = state.selectedFeatureId;
      const nextSelection =
        currentSelection && normalized.includes(currentSelection)
          ? currentSelection
          : (normalized.find((id) => Boolean(state.featuresById[id])) ?? null);

      if (
        !hasActiveFeatureIdsChanged &&
        nextSelection === state.selectedFeatureId
      ) {
        return;
      }

      set({
        activeFeatureIds: normalized,
        selectedFeatureId: nextSelection,
      });
    },
    clear() {
      set({ ...INITIAL_STATE });
    },
  }),
);

function mapFetchResultToState(
  prevState: FeatureGraphStoreState,
  result: FeatureGraphFetchResult,
) {
  const proposalState = mapFeatureProposalState(
    { proposals: result.proposals, activeProposalId: result.activeProposalId },
  );

  const proposalActions = pruneProposalActions(
    prevState.proposalActions,
    proposalState.proposals,
  );

  if (!result.graph) {
    return {
      ...prevState,
      graph: null,
      features: [],
      featuresById: {},
      testsByFeatureId: {},
      artifactsByFeatureId: {},
      activeFeatureIds: result.activeFeatureIds,
      proposals: proposalState.proposals,
      activeProposalId: proposalState.activeProposalId,
      proposalActions,
      selectedFeatureId: result.activeFeatureIds[0] ?? null,
      isLoading: false,
      isGeneratingGraph: false,
      error: null,
    } satisfies Partial<FeatureGraphStoreState>;
  }

  const features = result.graph.listFeatures();
  const featuresById: Record<string, FeatureNode> = {};
  for (const feature of features) {
    featuresById[feature.id] = feature;
  }

  const testsByFeatureId: Record<string, FeatureResource[]> = {};
  const artifactsByFeatureId: Record<string, FeatureResource[]> = {};

  for (const feature of features) {
    testsByFeatureId[feature.id] = dedupeResources(
      testsForFeature(result.graph, feature.id).map((ref, index) =>
        normalizeArtifactRef(ref, `Test ${index + 1}`),
      ),
    );

    artifactsByFeatureId[feature.id] = dedupeResources(
      collectFeatureArtifacts(feature.artifacts).map((ref, index) =>
        normalizeArtifactRef(ref, `Artifact ${index + 1}`),
      ),
    );
  }

  const selectedFeatureId = resolveSelectedFeatureId(
    prevState.selectedFeatureId,
    result.activeFeatureIds,
    features,
  );

  return {
    threadId: prevState.threadId,
    graph: result.graph,
    features,
    featuresById,
    testsByFeatureId,
    artifactsByFeatureId,
    activeFeatureIds: result.activeFeatureIds,
    proposals: proposalState.proposals,
    activeProposalId: proposalState.activeProposalId,
    proposalActions,
    selectedFeatureId,
    isLoading: false,
    isGeneratingGraph: false,
    error: null,
  } satisfies Partial<FeatureGraphStoreState>;
}

function pruneProposalActions(
  current: Record<string, ProposalActionState>,
  proposals: FeatureProposal[],
): Record<string, ProposalActionState> {
  const activeIds = new Set(proposals.map((proposal) => proposal.proposalId));

  return Object.fromEntries(
    Object.entries(current).filter(([proposalId]) => activeIds.has(proposalId)),
  );
}

function resolveSelectedFeatureId(
  currentSelection: string | null,
  activeFeatureIds: string[],
  features: FeatureNode[],
): string | null {
  if (
    currentSelection &&
    features.some((feature) => feature.id === currentSelection)
  ) {
    if (
      activeFeatureIds.length === 0 ||
      activeFeatureIds.includes(currentSelection)
    ) {
      return currentSelection;
    }
  }

  if (activeFeatureIds.length > 0) {
    const active = activeFeatureIds.find((id) =>
      features.some((feature) => feature.id === id),
    );
    if (active) {
      return active;
    }
  }

  return features[0]?.id ?? null;
}

function collectFeatureArtifacts(
  artifacts: ArtifactCollection | undefined,
): ArtifactRef[] {
  if (!artifacts) return [];

  if (Array.isArray(artifacts)) {
    return artifacts;
  }

  return Object.values(artifacts);
}

function normalizeArtifactRef(
  ref: ArtifactRef,
  fallbackLabel: string,
): FeatureResource {
  if (typeof ref === "string") {
    const label = ref.trim() || fallbackLabel;
    return {
      id: `string:${label}`,
      label,
    };
  }

  const label = pickFirst(
    ref.path,
    ref.name,
    ref.description,
    ref.url,
    ref.type,
    fallbackLabel,
  );

  const secondary = ref.path && ref.path !== label ? ref.path : undefined;
  const description =
    ref.description && ref.description !== label ? ref.description : undefined;

  const href = ref.url && isHttpUrl(ref.url) ? ref.url : undefined;

  return {
    id: `object:${ref.path ?? ref.url ?? label}`,
    label,
    secondaryLabel: secondary,
    description,
    href,
  };
}

function dedupeResources(resources: FeatureResource[]): FeatureResource[] {
  const map = new Map<string, FeatureResource>();
  for (const resource of resources) {
    if (!resource?.id) continue;
    if (!map.has(resource.id)) {
      map.set(resource.id, resource);
    }
  }
  return Array.from(map.values());
}

function pickFirst(...candidates: (string | undefined)[]): string {
  for (const candidate of candidates) {
    if (!candidate) continue;
    const trimmed = candidate.trim();
    if (trimmed) return trimmed;
  }
  return "";
}

function isHttpUrl(url: string): boolean {
  return /^https?:\/\//i.test(url.trim());
}
