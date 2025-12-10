"use client";

import { type ReactNode, useCallback, useMemo } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clock3,
  ExternalLink,
  FileText,
  Info,
  Layers,
  ListChecks,
  Loader2,
  Network,
  ThumbsDown,
  ThumbsUp,
  XCircle,
} from "lucide-react";

import { useShallow } from "zustand/react/shallow";

import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { calculateLastActivity } from "@/lib/thread-utils";
import { cn } from "@/lib/utils";
import {
  FeatureResource,
  FeatureRunState,
  FeatureRunStatus,
  useFeatureGraphStore,
} from "@/stores/feature-graph-store";
import type { FeatureNode } from "@openswe/shared/feature-graph/types";
import type { FeatureProposal } from "@openswe/shared/open-swe/manager/types";
import type { FeatureProposalAction } from "@/services/feature-graph.service";

const EMPTY_STATE_MESSAGE =
  "Feature insights will appear once the session resolves relevant features.";

type ProposalActionSnapshot = {
  status: "idle" | "pending" | "error";
  error?: string | null;
  message?: string | null;
  updatedAt: number;
};

export function FeatureInsightsPanel({
  onStartPlanner,
}: {
  onStartPlanner?: () => void;
}) {
  const {
    graph,
    features,
    featuresById,
    activeFeatureIds,
    proposals,
    activeProposalId,
    proposalActions,
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
    respondToProposal,
  } = useFeatureGraphStore(
    useShallow((state) => ({
      graph: state.graph,
      features: state.features,
      featuresById: state.featuresById,
      activeFeatureIds: state.activeFeatureIds,
      proposals: state.proposals,
      activeProposalId: state.activeProposalId,
      proposalActions: state.proposalActions,
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
      respondToProposal: state.respondToProposal,
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

  const sortedProposals = useMemo(
    () =>
      [...proposals].sort(
        (a, b) =>
          new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
      ),
    [proposals],
  );

  const pendingProposals = useMemo(
    () =>
      sortedProposals.filter((proposal) => proposal.status === "proposed"),
    [sortedProposals],
  );

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

  const handleProposalAction = useCallback(
    (proposalId: string, action: FeatureProposalAction) => {
      void respondToProposal(proposalId, action)
        .then((message) => {
          const fallback =
            action === "approve"
              ? "Proposal approved"
              : action === "reject"
                ? "Proposal rejected"
                : "Requested more information";
          toast.success(message ?? fallback);
        })
        .catch((error) => {
          const message =
            error instanceof Error
              ? error.message
              : "Failed to update proposal";
          toast.error(message);
        });
    },
    [respondToProposal],
  );

  const handleStartDevelopment = useCallback(() => {
    if (!selectedFeature) return;

    onStartPlanner?.();
    void startFeatureDevelopment(selectedFeature.id).catch((error) => {
      const baseMessage =
        error instanceof Error
          ? error.message
          : "Failed to start feature development";

      const normalized = baseMessage.toLowerCase();
      const isThreadBusy =
        normalized.includes("busy") || normalized.includes("running");

      const message = isThreadBusy
        ? `${baseMessage}. Cancel or finish the current design run in the Planner tab, then try again.`
        : baseMessage;

      toast.error(message);
    });
  }, [onStartPlanner, selectedFeature, startFeatureDevelopment]);

  const hasData =
    features.length > 0 ||
    activeFeatures.length > 0 ||
    activeFeatureIds.length > 0 ||
    proposals.length > 0;

  if (!hasData && !isLoading && !error) {
    return null;
  }

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

        {sortedProposals.length > 0 && (
          <ProposalSection
            proposals={sortedProposals}
            pendingCount={pendingProposals.length}
            activeProposalId={activeProposalId}
            proposalActions={proposalActions}
            onAction={handleProposalAction}
          />
        )}

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
                onStart={handleStartDevelopment}
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

function ProposalSection({
  proposals,
  pendingCount,
  activeProposalId,
  proposalActions,
  onAction,
}: {
  proposals: FeatureProposal[];
  pendingCount: number;
  activeProposalId: string | null;
  proposalActions: Record<string, ProposalActionSnapshot>;
  onAction: (proposalId: string, action: FeatureProposalAction) => void;
}) {
  if (proposals.length === 0) {
    return null;
  }

  return (
    <div className="border-border/70 bg-muted/30 rounded-md border p-3">
      <div className="text-muted-foreground flex items-center justify-between text-xs font-semibold uppercase tracking-wide">
        <div className="flex items-center gap-2">
          <Info className="size-4" />
          <span>Feature proposals</span>
        </div>
        <span>
          {pendingCount > 0
            ? `${pendingCount} pending`
            : `${proposals.length} total`}
        </span>
      </div>

      <div className="mt-3 flex flex-col gap-2">
        {proposals.map((proposal) => (
          <ProposalItem
            key={proposal.proposalId}
            proposal={proposal}
            isActive={proposal.proposalId === activeProposalId}
            actionState={proposalActions[proposal.proposalId]}
            onAction={onAction}
          />
        ))}
      </div>
    </div>
  );
}

function ProposalItem({
  proposal,
  isActive,
  actionState,
  onAction,
}: {
  proposal: FeatureProposal;
  isActive: boolean;
  actionState: ProposalActionSnapshot | undefined;
  onAction: (proposalId: string, action: FeatureProposalAction) => void;
}) {
  const isActionPending = actionState?.status === "pending";
  const hasError = actionState?.status === "error" && actionState.error;
  const showMessage = actionState?.status === "idle" && actionState.message;
  const actionsDisabled = isActionPending || proposal.status !== "proposed";

  const renderActionLabel = (
    label: string,
    icon: ReactNode,
    isLoading?: boolean,
  ) =>
    isLoading ? (
      <span className="flex items-center gap-2">
        <Loader2 className="size-4 animate-spin" />
        {label}
      </span>
    ) : (
      <span className="flex items-center gap-2">
        {icon}
        {label}
      </span>
    );

  return (
    <div className="border-border/60 bg-background/80 rounded-md border p-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm leading-tight font-semibold">
              {proposal.summary}
            </span>
            <ProposalStatusBadge
              status={proposal.status}
              isActive={isActive}
            />
          </div>
          <div className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
            <span className="font-mono text-[11px]">{proposal.featureId}</span>
            <span>• Updated {calculateLastActivity(proposal.updatedAt)}</span>
            {proposal.rationale && (
              <span className="line-clamp-2">{proposal.rationale}</span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 sm:justify-end">
          <Button
            size="sm"
            variant="secondary"
            disabled={actionsDisabled}
            onClick={() => onAction(proposal.proposalId, "info")}
          >
            {renderActionLabel(
              "More info",
              <Info className="size-4" />,
              isActionPending,
            )}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={actionsDisabled}
            onClick={() => onAction(proposal.proposalId, "reject")}
          >
            {renderActionLabel(
              "Reject",
              <ThumbsDown className="size-4" />,
              isActionPending,
            )}
          </Button>
          <Button
            size="sm"
            disabled={actionsDisabled}
            onClick={() => onAction(proposal.proposalId, "approve")}
          >
            {renderActionLabel(
              "Approve",
              <ThumbsUp className="size-4" />,
              isActionPending,
            )}
          </Button>
        </div>
      </div>

      {hasError && (
        <div className="text-destructive mt-2 text-xs">{actionState?.error}</div>
      )}

      {showMessage && (
        <div className="text-muted-foreground mt-2 text-xs">
          {actionState?.message}
        </div>
      )}
    </div>
  );
}

function ProposalStatusBadge({
  status,
  isActive,
}: {
  status: FeatureProposal["status"];
  isActive: boolean;
}) {
  const tone = (() => {
    switch (status) {
      case "approved":
        return {
          icon: <CheckCircle2 className="size-3" />,
          className: "bg-emerald-100 text-emerald-800",
          label: "Approved",
        };
      case "rejected":
        return {
          icon: <XCircle className="size-3" />,
          className: "bg-red-100 text-red-800",
          label: "Rejected",
        };
      default:
        return {
          icon: <Clock3 className="size-3" />,
          className: "bg-amber-100 text-amber-800",
          label: "Pending",
        };
    }
  })();

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold",
        tone.className,
        isActive ? "ring-2 ring-offset-1 ring-primary/40" : "",
      )}
    >
      {tone.icon}
      <span>{tone.label}</span>
      {isActive && (
        <span className="text-muted-foreground/80 text-[10px] font-medium">
          Active
        </span>
      )}
    </span>
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
}: {
  feature: FeatureNode;
  runState: FeatureRunState | undefined;
  onStart: () => void;
}) {
  const status = runState?.status ?? "idle";
  const isRunning = status === "running" || status === "starting";
  const isBlocked = status === "error";
  const isDisabled = isRunning || isBlocked;

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
          disabled={isDisabled}
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
          <p className="text-muted-foreground text-sm">
            Planner output is available in the Planner tab. Open it to view the
            current run and share feedback.
          </p>
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

  const selectedFeature = features.find((feature) => feature.id === selectedId);

  return (
    <div className="flex flex-col gap-2">
      <div className="text-muted-foreground flex items-center justify-between text-xs font-medium tracking-wide uppercase">
        <span>
          {hasActiveFeatures ? "Active features" : "Available features"}
        </span>
        <span>{features.length}</span>
      </div>
      <Select
        value={selectedId ?? undefined}
        onValueChange={onSelect}
      >
        <SelectTrigger className="w-full justify-between">
          <div className="flex flex-col text-left">
            <span className="text-sm leading-tight font-medium">
              {selectedFeature ? selectedFeature.name : "Select a feature"}
            </span>
            <span className="text-muted-foreground font-mono text-[11px]">
              {selectedFeature
                ? selectedFeature.id
                : "Choose a feature to view details"}
            </span>
          </div>
          {selectedId && (
            <FeatureRunStatusPill status={featureRuns[selectedId]?.status} />
          )}
        </SelectTrigger>
        <SelectContent className="w-full min-w-[18rem]">
          <SelectGroup>
            {features.map((feature) => (
              <SelectItem
                key={feature.id}
                value={feature.id}
              >
                <div className="flex flex-col gap-1 text-left">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm leading-tight font-medium">
                      {feature.name}
                    </span>
                    <FeatureRunStatusPill
                      status={featureRuns[feature.id]?.status}
                    />
                  </div>
                  <span className="text-muted-foreground font-mono text-[11px]">
                    {feature.id}
                  </span>
                </div>
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
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
                "border-border/70 hover:bg-muted/60 focus-visible:ring-ring flex flex-col gap-1 rounded-md border px-3 py-2 text-left shadow-xs transition-colors focus-visible:ring-2 focus-visible:outline-none whitespace-normal break-words",
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
