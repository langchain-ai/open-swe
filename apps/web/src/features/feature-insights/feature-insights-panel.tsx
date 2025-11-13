"use client";

import { type ReactNode, useMemo } from "react";
import {
  AlertCircle,
  ExternalLink,
  FileText,
  Layers,
  ListChecks,
  Network,
} from "lucide-react";

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
import { cn } from "@/lib/utils";
import {
  FeatureResource,
  useFeatureGraphStore,
} from "@/stores/feature-graph-store";
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
    isLoading,
    error,
    threadId,
    fetchGraphForThread,
    selectFeature,
  } = useFeatureGraphStore((state) => ({
    graph: state.graph,
    features: state.features,
    featuresById: state.featuresById,
    activeFeatureIds: state.activeFeatureIds,
    selectedFeatureId: state.selectedFeatureId,
    testsByFeatureId: state.testsByFeatureId,
    artifactsByFeatureId: state.artifactsByFeatureId,
    isLoading: state.isLoading,
    error: state.error,
    threadId: state.threadId,
    fetchGraphForThread: state.fetchGraphForThread,
    selectFeature: state.selectFeature,
  }));

  const activeFeatures = useMemo(
    () =>
      activeFeatureIds
        .map((id) => featuresById[id])
        .filter((feature): feature is NonNullable<typeof feature> => Boolean(feature)),
    [activeFeatureIds, featuresById],
  );

  const selectedFeature = selectedFeatureId
    ? featuresById[selectedFeatureId]
    : undefined;

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
    ? testsByFeatureId[selectedFeatureId] ?? []
    : [];
  const artifacts = selectedFeatureId
    ? artifactsByFeatureId[selectedFeatureId] ?? []
    : [];

  const hasData = features.length > 0 || activeFeatures.length > 0;

  if (!hasData && !isLoading && !error) {
    return null;
  }

  const handleRetry = () => {
    if (threadId) {
      void fetchGraphForThread(threadId, { force: true });
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
        {isLoading && !selectedFeature && <LoadingState />}

        {!isLoading && error && (
          <ErrorState message={error} onRetry={handleRetry} />
        )}

        {!isLoading && !error && !selectedFeature && (
          <EmptyState message={EMPTY_STATE_MESSAGE} />
        )}

        {selectedFeature && (
          <div className="flex flex-col gap-4">
            <FeatureSummary
              feature={selectedFeature}
              isActive={activeFeatureIds.includes(selectedFeature.id)}
            />

            <FeatureSelection
              features={activeFeatures.length > 0 ? activeFeatures : features}
              selectedId={selectedFeatureId}
              onSelect={selectFeature}
              hasActiveFeatures={activeFeatures.length > 0}
            />

            <Separator className="bg-border/60" />

            <DependencySection
              title="Upstream dependencies"
              description="Features that must be in place before this work can succeed."
              icon={<Network className="size-4" />}
              features={upstreamDependencies}
              onSelect={selectFeature}
            />

            <DependencySection
              title="Downstream impact"
              description="Features that rely on the current feature and may require verification."
              icon={<Network className="size-4 rotate-180" />}
              features={downstreamDependencies}
              onSelect={selectFeature}
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
          </div>
        )}
      </CardContent>
    </Card>
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
        <h3 className="text-lg font-semibold leading-tight">{feature.name}</h3>
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
          <span className="rounded-full bg-muted px-2 py-0.5 text-[11px]">
            {feature.group}
          </span>
        )}
      </div>
    </div>
  );
}

function FeatureSelection({
  features,
  selectedId,
  onSelect,
  hasActiveFeatures,
}: {
  features: FeatureNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  hasActiveFeatures: boolean;
}) {
  if (features.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between text-xs font-medium uppercase tracking-wide text-muted-foreground">
        <span>{hasActiveFeatures ? "Active features" : "Available features"}</span>
        <span>{features.length}</span>
      </div>
      <ScrollArea className="max-h-32 rounded-md border border-border/60">
        <div className="flex flex-wrap gap-2 p-3">
          {features.map((feature) => (
            <Button
              key={feature.id}
              size="sm"
              variant={feature.id === selectedId ? "secondary" : "outline"}
              className="h-auto min-w-[8rem] flex-1 flex-col items-start gap-0.5 px-3 py-2 text-left"
              onClick={() => onSelect(feature.id)}
            >
              <span className="text-sm font-medium leading-tight">
                {feature.name}
              </span>
              <span className="text-muted-foreground text-[11px] font-mono">
                {feature.id}
              </span>
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
}: {
  title: string;
  description: string;
  icon: ReactNode;
  features: FeatureNode[];
  onSelect: (id: string) => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      <SectionHeader title={title} description={description} icon={icon} />
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
                "border-border/70 hover:bg-muted/60 focus-visible:ring-ring flex flex-col gap-1 rounded-md border px-3 py-2 text-left shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2",
              )}
              onClick={() => onSelect(feature.id)}
            >
              <span className="text-sm font-medium leading-tight">
                {feature.name}
              </span>
              <span className="text-muted-foreground text-[11px] font-mono">
                {feature.id}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
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
      <SectionHeader title={title} description={description} icon={icon} />
      {resources.length === 0 ? (
        <p className="text-muted-foreground text-sm">No suggestions available.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {resources.map((resource) => (
            <li key={resource.id} className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium leading-tight">
                  {resource.label}
                </p>
                {resource.secondaryLabel && (
                  <p className="text-muted-foreground text-xs font-mono">
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
      <div className="text-muted-foreground rounded-md border border-border/60 bg-muted/40 p-2">
        {icon}
      </div>
      <div className="flex flex-col">
        <h4 className="text-sm font-semibold leading-tight">{title}</h4>
        <p className="text-muted-foreground text-sm leading-snug">{description}</p>
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
    <div className="text-muted-foreground flex items-center gap-3 rounded-md border border-dashed border-border/70 bg-muted/20 px-4 py-3 text-sm">
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
    <div className="flex flex-col gap-3 rounded-md border border-destructive/40 bg-destructive/5 px-4 py-3">
      <div className="flex items-start gap-2">
        <AlertCircle className="text-destructive size-4" />
        <div className="flex flex-col gap-1">
          <span className="text-sm font-semibold text-destructive">
            Unable to load feature graph
          </span>
          <span className="text-muted-foreground text-sm">{message}</span>
        </div>
      </div>
      <div>
        <Button size="sm" variant="outline" onClick={onRetry}>
          Retry
        </Button>
      </div>
    </div>
  );
}

