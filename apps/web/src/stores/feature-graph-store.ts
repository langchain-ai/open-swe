import { create } from "zustand";

import { FeatureGraph, testsForFeature } from "@openswe/shared/feature-graph";
import type {
  ArtifactCollection,
  ArtifactRef,
  FeatureNode,
} from "@openswe/shared/feature-graph/types";

import type { FeatureGraphFetchResult } from "@/services/feature-graph.service";
import { fetchFeatureGraph } from "@/services/feature-graph.service";

export type FeatureResource = {
  id: string;
  label: string;
  secondaryLabel?: string;
  description?: string;
  href?: string;
};

interface FeatureGraphStoreState {
  threadId: string | null;
  graph: FeatureGraph | null;
  features: FeatureNode[];
  featuresById: Record<string, FeatureNode>;
  activeFeatureIds: string[];
  selectedFeatureId: string | null;
  testsByFeatureId: Record<string, FeatureResource[]>;
  artifactsByFeatureId: Record<string, FeatureResource[]>;
  isLoading: boolean;
  error: string | null;
  fetchGraphForThread: (
    threadId: string,
    options?: { force?: boolean },
  ) => Promise<void>;
  selectFeature: (featureId: string | null) => void;
  setActiveFeatureIds: (featureIds?: string[] | null) => void;
  clear: () => void;
}

const INITIAL_STATE: Omit<FeatureGraphStoreState, "fetchGraphForThread" | "selectFeature" | "setActiveFeatureIds" | "clear"> = {
  threadId: null,
  graph: null,
  features: [],
  featuresById: {},
  activeFeatureIds: [],
  selectedFeatureId: null,
  testsByFeatureId: {},
  artifactsByFeatureId: {},
  isLoading: false,
  error: null,
};

export const useFeatureGraphStore = create<FeatureGraphStoreState>((set, get) => ({
  ...INITIAL_STATE,
  async fetchGraphForThread(threadId, options) {
    const { threadId: currentThreadId, isLoading, graph } = get();
    const shouldSkip =
      !threadId ||
      (!options?.force &&
        threadId === currentThreadId &&
        (graph !== null || isLoading));

    if (shouldSkip) return;

    set({
      ...INITIAL_STATE,
      threadId,
      isLoading: true,
    });

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
        error: message,
        graph: null,
        features: [],
        featuresById: {},
        testsByFeatureId: {},
        artifactsByFeatureId: {},
        activeFeatureIds: [],
        selectedFeatureId: null,
      });
    }
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
    set((state) => {
      if (normalized.length === 0) {
        return {
          activeFeatureIds: [],
          selectedFeatureId: state.selectedFeatureId,
        };
      }

      const currentSelection = state.selectedFeatureId;
      const nextSelection =
        currentSelection && normalized.includes(currentSelection)
          ? currentSelection
          : normalized[0];

      return {
        activeFeatureIds: normalized,
        selectedFeatureId: nextSelection,
      };
    });
  },
  clear() {
    set({ ...INITIAL_STATE });
  },
}));

function mapFetchResultToState(
  prevState: FeatureGraphStoreState,
  result: FeatureGraphFetchResult,
) {
  if (!result.graph) {
    return {
      ...prevState,
      graph: null,
      features: [],
      featuresById: {},
      testsByFeatureId: {},
      artifactsByFeatureId: {},
      activeFeatureIds: result.activeFeatureIds,
      selectedFeatureId: result.activeFeatureIds[0] ?? null,
      isLoading: false,
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
    selectedFeatureId,
    isLoading: false,
    error: null,
  } satisfies Partial<FeatureGraphStoreState>;
}

function resolveSelectedFeatureId(
  currentSelection: string | null,
  activeFeatureIds: string[],
  features: FeatureNode[],
): string | null {
  if (currentSelection && features.some((feature) => feature.id === currentSelection)) {
    if (activeFeatureIds.length === 0 || activeFeatureIds.includes(currentSelection)) {
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

function normalizeFeatureIds(value?: string[] | null): string[] {
  if (!Array.isArray(value)) return [];

  const seen = new Set<string>();
  const normalized: string[] = [];

  for (const entry of value) {
    if (typeof entry !== "string") continue;
    const trimmed = entry.trim();
    if (!trimmed) continue;
    const key = trimmed.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    normalized.push(trimmed);
  }

  return normalized;
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
  const description = ref.description && ref.description !== label ? ref.description : undefined;

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
