"use client";

import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  FileText,
  Layers,
  ListChecks,
  Loader2,
  Network,
} from "lucide-react";

import { useShallow } from "zustand/react/shallow";

import { useStream } from "@langchain/langgraph-sdk/react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { ActionsRenderer } from "@/components/v2/actions-renderer";
import { cn } from "@/lib/utils";
import {
  FeatureResource,
  FeatureRunState,
  FeatureRunStatus,
  useFeatureGraphStore,
} from "@/stores/feature-graph-store";
import { LOCAL_MODE_HEADER, PLANNER_GRAPH_ID } from "@openswe/shared/constants";
import {
  CustomNodeEvent,
  isCustomNodeEvent,
} from "@openswe/shared/open-swe/custom-node-events";
import type { PlannerGraphState } from "@openswe/shared/open-swe/planner/types";
import type { FeatureNode } from "@openswe/shared/feature-graph/types";

const EMPTY_STATE_MESSAGE =
  "Feature insights will appear once the session resolves relevant features.";

export function FeatureInsightsPanel() {
  const {
    graph,
    features,
    featuresById,
    activeFeatureIds,
    selectedFeatureId,
    testsByFeatureId,
    artifactsByFeatureId,
    featureRuns,
    isLoading,
    isGeneratingGraph,
    error,
    threadId,
    fetchGraphForThread,
    requestGraphGeneration,
    startFeatureDevelopment,
    selectFeature,
    setFeatureRunStatus,
  } = useFeatureGraphStore(
    useShallow((state) => ({
      graph: state.graph,
      features: state.features,
      featuresById: state.featuresById,
      activeFeatureIds: state.activeFeatureIds,
      selectedFeatureId: state.selectedFeatureId,
      testsByFeatureId: state.testsByFeatureId,
      artifactsByFeatureId: state.artifactsByFeatureId,
      featureRuns: state.featureRuns,
      isLoading: state.isLoading,
      isGeneratingGraph: state.isGeneratingGraph,
      error: state.error,
      threadId: state.threadId,
      fetchGraphForThread: state.fetchGraphForThread,
      requestGraphGeneration: state.requestGraphGeneration,
      startFeatureDevelopment: state.startFeatureDevelopment,
      selectFeature: state.selectFeature,
      setFeatureRunStatus: state.setFeatureRunStatus,
    })),
  );

  const activeFeatures = useMemo(
    () =>
      activeFeatureIds
        .map((id) => featuresById[id])
        .filter((feature): feature is NonNullable<typeof feature> =>
          Boolean(feature),
        ),
    [activeFeatureIds, featuresById],
  );

  const selectedFeature = selectedFeatureId
    ? featuresById[selectedFeatureId]
    : undefined;

  const selectionCandidates =
    activeFeatures.length > 0 ? activeFeatures : features;

  const selectedRunState =
    selectedFeatureId && featureRuns[selectedFeatureId]
      ? featureRuns[selectedFeatureId]
      : undefined;

  const [featureNodeEvents, setFeatureNodeEvents] = useState<
    Record<string, CustomNodeEvent[]>
  >({});

  const selectedCustomEvents = useMemo(
    () =>
      selectedFeatureId && featureNodeEvents[selectedFeatureId]
        ? featureNodeEvents[selectedFeatureId]
        : [],
    [featureNodeEvents, selectedFeatureId],
  );

  const setSelectedCustomEvents = useCallback(
    (
      updater:
        | CustomNodeEvent[]
        | ((events: CustomNodeEvent[]) => CustomNodeEvent[]),
    ) => {
      if (!selectedFeatureId) return;

      setFeatureNodeEvents((prev) => {
        const current = prev[selectedFeatureId] ?? [];
        const next = typeof updater === "function" ? updater(current) : updater;
        return {
          ...prev,
          [selectedFeatureId]: next,
        };
      });
    },
    [selectedFeatureId],
  );

  const featureRunStream = useStream<PlannerGraphState>({
    apiUrl: process.env.NEXT_PUBLIC_API_URL,
    assistantId: PLANNER_GRAPH_ID,
    reconnectOnMount: true,
    threadId: selectedRunState?.threadId ?? undefined,
    onCustomEvent: (event) => {
      if (isCustomNodeEvent(event) && selectedFeatureId) {
        setFeatureNodeEvents((prev) => {
          const existing = prev[selectedFeatureId] ?? [];
          if (existing.some((entry) => entry.actionId === event.actionId)) {
            return prev;
          }
          return {
            ...prev,
            [selectedFeatureId]: [...existing, event],
          };
        });
      }
    },
    fetchStateHistory: false,
    defaultHeaders: { [LOCAL_MODE_HEADER]: "true" },
  });

  const joinedFeatureRunId = useRef<string | undefined>(undefined);

  useEffect(() => {
    if (
      selectedRunState?.runId &&
      selectedRunState.runId !== joinedFeatureRunId.current
    ) {
      joinedFeatureRunId.current = selectedRunState.runId;
      featureRunStream.joinStream(selectedRunState.runId).catch(() => {});
    } else if (!selectedRunState?.runId) {
      joinedFeatureRunId.current = undefined;
    }
  }, [featureRunStream, selectedRunState?.runId]);

  useEffect(() => {
    if (!selectedFeatureId || !selectedRunState) return;

    if (featureRunStream.error) {
      const message =
        typeof featureRunStream.error === "object" &&
        featureRunStream.error &&
        "message" in featureRunStream.error
          ? String((featureRunStream.error as Error).message)
          : "Feature development run encountered an error";
      setFeatureRunStatus(selectedFeatureId, "error", {
        runId: selectedRunState.runId,
        threadId: selectedRunState.threadId,
        error: message,
      });
      return;
    }

    if (featureRunStream.isLoading) {
      setFeatureRunStatus(selectedFeatureId, "running", {
        runId: selectedRunState.runId,
        threadId: selectedRunState.threadId,
      });
    } else if ((featureRunStream.messages?.length ?? 0) > 0) {
      setFeatureRunStatus(selectedFeatureId, "completed", {
        runId: selectedRunState.runId,
        threadId: selectedRunState.threadId,
      });
    }
  }, [
    featureRunStream.error,
    featureRunStream.isLoading,
    featureRunStream.messages,
    selectedFeatureId,
    selectedRunState,
    setFeatureRunStatus,
  ]);

  const upstreamDependencies = useMemo(() => {
    if (!graph || !selectedFeature) return [];
    return graph
      .getNeighbors(selectedFeature.id, "upstream")
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [graph, selectedFeature]);

  const downstreamDependencies = useMemo(() => {
    if (!graph || !selectedFeature) return [];
    return graph
      .getNeighbors(selectedFeature.id, "downstream")
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [graph, selectedFeature]);

  const tests = selectedFeatureId
    ? (testsByFeatureId[selectedFeatureId] ?? [])
    : [];
  const artifacts = selectedFeatureId
    ? (artifactsByFeatureId[selectedFeatureId] ?? [])
    : [];

  const hasData =
    features.length > 0 ||
    activeFeatures.length > 0 ||
    activeFeatureIds.length > 0;

  if (!hasData && !isLoading && !error) {
    return null;
  }

  const handleRetry = () => {
    if (threadId) {
      void fetchGraphForThread(threadId, { force: true });
    }
  };

  const handleGenerate = () => {
    if (threadId) {
      void requestGraphGeneration(threadId);
    }
  };

  return (
    <Card className="bg-card/95 border-border/80 shadow-sm">
      <CardHeader className="gap-2 pb-4">
        <div className="flex items-center gap-2">
          <Layers className="text-muted-foreground size-4" />
          <CardTitle className="text-base">Feature insights</CardTitle>
        </div>
        <CardDescription>
          Understand related work, dependencies, and validation hints for the
          active session.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 pb-6">
        <GraphInitializationStatus
          isLoading={isLoading}
          hasGraph={Boolean(graph)}
          totalFeatures={features.length}
          activeFeatures={activeFeatureIds.length}
          onRetry={handleRetry}
          onGenerate={handleGenerate}
          isGeneratingGraph={isGeneratingGraph}
        />

        {isLoading && !selectedFeature && <LoadingState />}

        {!isLoading && error && (
          <ErrorState
            message={error}
            onRetry={handleRetry}
          />
        )}

        {!isLoading && !error && selectionCandidates.length === 0 && (
          <EmptyState message={EMPTY_STATE_MESSAGE} />
        )}

        {selectionCandidates.length > 0 && (
          <div className="flex flex-col gap-4">
            {selectedFeature && (
              <FeatureSummary
                feature={selectedFeature}
                isActive={activeFeatureIds.includes(selectedFeature.id)}
              />
            )}

            <FeatureSelection
              features={selectionCandidates}
              selectedId={selectedFeatureId}
              onSelect={selectFeature}
              featureRuns={featureRuns}
              hasActiveFeatures={activeFeatures.length > 0}
            />

            {selectedFeature && (
              <FeatureDevelopmentPanel
                feature={selectedFeature}
                runState={selectedRunState}
                onStart={() => startFeatureDevelopment(selectedFeature.id)}
                stream={featureRunStream}
                customNodeEvents={selectedCustomEvents}
                setCustomNodeEvents={setSelectedCustomEvents}
              />
            )}

            {selectedFeature && <Separator className="bg-border/60" />}

            {selectedFeature && (
              <>
                <DependencySection
                  title="Upstream dependencies"
                  description="Features that must be in place before this work can succeed."
                  icon={<Network className="size-4" />}
                  features={upstreamDependencies}
                  onSelect={selectFeature}
                  featureRuns={featureRuns}
                />

                <DependencySection
                  title="Downstream impact"
                  description="Features that rely on the current feature and may require verification."
                  icon={<Network className="size-4 rotate-180" />}
                  features={downstreamDependencies}
                  onSelect={selectFeature}
                  featureRuns={featureRuns}
                />

                <ResourceSection
                  title="Suggested tests"
                  description="Run these tests to validate the feature’s behaviour."
                  icon={<ListChecks className="size-4" />}
                  resources={tests}
                />

                <ResourceSection
                  title="Related artifacts"
                  description="Review these documents, manifests, or code artifacts for additional context."
                  icon={<FileText className="size-4" />}
                  resources={artifacts}
                />
              </>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function GraphInitializationStatus({
  isLoading,
  hasGraph,
  totalFeatures,
  activeFeatures,
  onRetry,
  onGenerate,
  isGeneratingGraph,
}: {
  isLoading: boolean;
  hasGraph: boolean;
  totalFeatures: number;
  activeFeatures: number;
  onRetry: () => void;
  onGenerate: () => void;
  isGeneratingGraph: boolean;
}) {
  const statusIcon = isLoading ? (
    <Loader2 className="size-4 animate-spin" />
  ) : hasGraph ? (
    <CheckCircle2 className="size-4 text-emerald-500" />
  ) : (
    <AlertCircle className="size-4 text-amber-500" />
  );

  return (
    <div className="border-border/70 bg-muted/40 rounded-md border p-3">
      <div className="text-muted-foreground flex items-center gap-2 text-xs font-medium tracking-wide uppercase">
        {statusIcon}
        <span>Graph initialization</span>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        <StatusTile
          label="Graph state"
          value={hasGraph ? "Loaded" : isLoading ? "Loading" : "Not available"}
          tone={hasGraph ? "positive" : "neutral"}
        />
        <StatusTile
          label="Defined features"
          value={`${totalFeatures}`}
          tone={totalFeatures > 0 ? "positive" : "neutral"}
        />
        <StatusTile
          label="Active features"
          value={`${activeFeatures}`}
          tone={activeFeatures > 0 ? "positive" : "neutral"}
        />
      </div>
      {!hasGraph && !isLoading && (
        <div className="border-border/70 bg-background/60 mt-3 flex items-center justify-between gap-3 rounded-md border border-dashed px-3 py-2 text-sm">
          <span className="text-muted-foreground">
            No feature graph data is attached to this thread yet.
          </span>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={onRetry}
              disabled={isGeneratingGraph || isLoading}
            >
              Reload
            </Button>
            <Button
              size="sm"
              onClick={onGenerate}
              disabled={isGeneratingGraph || isLoading}
            >
              {isGeneratingGraph ? (
                <span className="flex items-center gap-2">
                  <Loader2 className="size-4 animate-spin" />
                  Generating
                </span>
              ) : (
                "Generate feature graph"
              )}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function StatusTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "positive" | "neutral";
}) {
  return (
    <div
      className={cn(
        "border-border/70 bg-background/80 flex flex-col rounded-md border px-3 py-2 text-sm",
        tone === "positive" &&
          "border-emerald-400/50 bg-emerald-50/70 dark:bg-emerald-950/30",
      )}
    >
      <span className="text-muted-foreground text-xs tracking-wide uppercase">
        {label}
      </span>
      <span className="font-semibold">{value}</span>
    </div>
  );
}

function FeatureSummary({
  feature,
  isActive,
}: {
  feature: FeatureNode;
  isActive: boolean;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <h3 className="text-lg leading-tight font-semibold">{feature.name}</h3>
        <Badge variant={isActive ? "secondary" : "outline"}>
          {isActive ? "Active" : feature.status}
        </Badge>
      </div>
      <p className="text-muted-foreground text-sm leading-relaxed">
        {feature.description}
      </p>
      <div className="text-muted-foreground flex flex-wrap items-center gap-3 text-xs">
        <span className="font-mono text-[11px]">{feature.id}</span>
        {feature.group && (
          <span className="bg-muted rounded-full px-2 py-0.5 text-[11px]">
            {feature.group}
          </span>
        )}
      </div>
    </div>
  );
}

function FeatureDevelopmentPanel({
  feature,
  runState,
  onStart,
  stream,
  customNodeEvents,
  setCustomNodeEvents,
}: {
  feature: FeatureNode;
  runState: FeatureRunState | undefined;
  onStart: () => void;
  stream: ReturnType<typeof useStream<PlannerGraphState>>;
  customNodeEvents: CustomNodeEvent[];
  setCustomNodeEvents: (
    events:
      | CustomNodeEvent[]
      | ((events: CustomNodeEvent[]) => CustomNodeEvent[]),
  ) => void;
}) {
  const status = runState?.status ?? "idle";
  const isRunning = status === "running" || status === "starting";

  return (
    <div className="border-border/70 bg-muted/20 rounded-md border p-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-2">
          <ListChecks className="text-muted-foreground size-4" />
          <div className="flex flex-col">
            <span className="text-sm leading-tight font-semibold">
              Feature development
            </span>
            <span className="text-muted-foreground text-xs">
              Launch a dedicated planner run to work on this feature.
            </span>
          </div>
        </div>
        <Button
          size="sm"
          onClick={onStart}
          disabled={isRunning}
          variant={status === "error" ? "destructive" : "default"}
        >
          {isRunning ? (
            <span className="flex items-center gap-2">
              <Loader2 className="size-4 animate-spin" />
              {status === "starting" ? "Starting" : "Running"}
            </span>
          ) : (
            "Start development"
          )}
        </Button>
      </div>

      <div className="text-muted-foreground mt-3 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-foreground font-semibold">{feature.name}</span>
        <FeatureRunStatusPill status={status} />
        {runState?.runId && (
          <span className="bg-background rounded px-2 py-1 font-mono text-[11px]">
            {runState.runId}
          </span>
        )}
      </div>

      {runState?.error && (
        <div className="border-destructive/40 bg-destructive/5 text-destructive mt-2 rounded-md border px-3 py-2 text-sm">
          {runState.error}
        </div>
      )}

      <div className="border-border/60 bg-background/70 mt-3 rounded-md border p-2">
        {runState?.runId && runState.threadId ? (
          <ActionsRenderer<PlannerGraphState>
            runId={runState.runId}
            customNodeEvents={customNodeEvents}
            setCustomNodeEvents={setCustomNodeEvents}
            stream={stream}
            threadId={runState.threadId}
          />
        ) : (
          <p className="text-muted-foreground text-sm">
            Start development to stream planner progress and provide feedback.
          </p>
        )}
      </div>
    </div>
  );
}

function FeatureSelection({
  features,
  selectedId,
  onSelect,
  featureRuns,
  hasActiveFeatures,
}: {
  features: FeatureNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  featureRuns: Record<string, FeatureRunState>;
  hasActiveFeatures: boolean;
}) {
  if (features.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="text-muted-foreground flex items-center justify-between text-xs font-medium tracking-wide uppercase">
        <span>
          {hasActiveFeatures ? "Active features" : "Available features"}
        </span>
        <span>{features.length}</span>
      </div>
      <ScrollArea className="border-border/60 max-h-32 rounded-md border">
        <div className="flex flex-wrap gap-2 p-3">
          {features.map((feature) => (
            <Button
              key={feature.id}
              size="sm"
              variant={feature.id === selectedId ? "secondary" : "outline"}
              className="h-auto min-w-[8rem] flex-1 flex-col items-start gap-0.5 px-3 py-2 text-left"
              onClick={() => onSelect(feature.id)}
            >
              <span className="text-sm leading-tight font-medium">
                {feature.name}
              </span>
              <span className="text-muted-foreground font-mono text-[11px]">
                {feature.id}
              </span>
              <FeatureRunStatusPill status={featureRuns[feature.id]?.status} />
            </Button>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}

function DependencySection({
  title,
  description,
  icon,
  features,
  onSelect,
  featureRuns,
}: {
  title: string;
  description: string;
  icon: ReactNode;
  features: FeatureNode[];
  onSelect: (id: string) => void;
  featureRuns: Record<string, FeatureRunState>;
}) {
  return (
    <div className="flex flex-col gap-3">
      <SectionHeader
        title={title}
        description={description}
        icon={icon}
      />
      {features.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No related features found.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {features.map((feature) => (
            <button
              key={feature.id}
              type="button"
              className={cn(
                "border-border/70 hover:bg-muted/60 focus-visible:ring-ring flex flex-col gap-1 rounded-md border px-3 py-2 text-left shadow-xs transition-colors focus-visible:ring-2 focus-visible:outline-none",
              )}
              onClick={() => onSelect(feature.id)}
            >
              <span className="text-sm leading-tight font-medium">
                {feature.name}
              </span>
              <span className="text-muted-foreground font-mono text-[11px]">
                {feature.id}
              </span>
              <FeatureRunStatusPill status={featureRuns[feature.id]?.status} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function FeatureRunStatusPill({ status }: { status?: FeatureRunStatus }) {
  if (!status || status === "idle") return null;

  const { label, tone, icon } = (() => {
    switch (status) {
      case "starting":
        return {
          label: "Starting",
          tone: "neutral" as const,
          icon: <Loader2 className="size-3 animate-spin" />,
        };
      case "running":
        return {
          label: "Running",
          tone: "neutral" as const,
          icon: <Loader2 className="size-3 animate-spin" />,
        };
      case "completed":
        return {
          label: "Completed",
          tone: "positive" as const,
          icon: <CheckCircle2 className="size-3" />,
        };
      case "error":
        return {
          label: "Error",
          tone: "warning" as const,
          icon: <AlertCircle className="size-3" />,
        };
      default:
        return {
          label: status,
          tone: "neutral" as const,
          icon: null,
        };
    }
  })();

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
        tone === "positive" &&
          "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200",
        tone === "warning" &&
          "bg-amber-50 text-amber-700 dark:bg-amber-900/40 dark:text-amber-200",
        tone === "neutral" && "bg-muted text-foreground",
      )}
    >
      {icon}
      {label}
    </span>
  );
}

function ResourceSection({
  title,
  description,
  icon,
  resources,
}: {
  title: string;
  description: string;
  icon: ReactNode;
  resources: FeatureResource[];
}) {
  return (
    <div className="flex flex-col gap-3">
      <SectionHeader
        title={title}
        description={description}
        icon={icon}
      />
      {resources.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No suggestions available.
        </p>
      ) : (
        <ul className="flex flex-col gap-3">
          {resources.map((resource) => (
            <li
              key={resource.id}
              className="flex items-start justify-between gap-3"
            >
              <div className="min-w-0 flex-1">
                <p className="text-sm leading-tight font-medium">
                  {resource.label}
                </p>
                {resource.secondaryLabel && (
                  <p className="text-muted-foreground font-mono text-xs">
                    {resource.secondaryLabel}
                  </p>
                )}
                {resource.description && (
                  <p className="text-muted-foreground text-xs">
                    {resource.description}
                  </p>
                )}
              </div>
              {resource.href && (
                <Button
                  asChild
                  variant="ghost"
                  size="icon"
                  className="text-muted-foreground size-8"
                >
                  <a
                    href={resource.href}
                    target="_blank"
                    rel="noreferrer"
                    aria-label={`Open ${resource.label}`}
                  >
                    <ExternalLink className="size-4" />
                  </a>
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SectionHeader({
  title,
  description,
  icon,
}: {
  title: string;
  description: string;
  icon: ReactNode;
}) {
  return (
    <div className="flex items-start gap-3">
      <div className="text-muted-foreground border-border/60 bg-muted/40 rounded-md border p-2">
        {icon}
      </div>
      <div className="flex flex-col">
        <h4 className="text-sm leading-tight font-semibold">{title}</h4>
        <p className="text-muted-foreground text-sm leading-snug">
          {description}
        </p>
      </div>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="flex flex-col gap-3">
      <Skeleton className="h-6 w-40" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-3/4" />
      <div className="flex gap-2">
        <Skeleton className="h-10 w-32" />
        <Skeleton className="h-10 w-32" />
      </div>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="text-muted-foreground border-border/70 bg-muted/20 flex items-center gap-3 rounded-md border border-dashed px-4 py-3 text-sm">
      <Layers className="size-4" />
      <span>{message}</span>
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="border-destructive/40 bg-destructive/5 flex flex-col gap-3 rounded-md border px-4 py-3">
      <div className="flex items-start gap-2">
        <AlertCircle className="text-destructive size-4" />
        <div className="flex flex-col gap-1">
          <span className="text-destructive text-sm font-semibold">
            Unable to load feature graph
          </span>
          <span className="text-muted-foreground text-sm">{message}</span>
        </div>
      </div>
      <div>
        <Button
          size="sm"
          variant="outline"
          onClick={onRetry}
        >
          Retry
        </Button>
      </div>
    </div>
  );
}
