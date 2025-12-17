import { randomUUID } from "crypto";
import { NextRequest, NextResponse } from "next/server";
import { Client, StreamMode } from "@langchain/langgraph-sdk";
import {
  LOCAL_MODE_HEADER,
  OPEN_SWE_STREAM_MODE,
  DESIGN_GRAPH_ID,
  PLANNER_GRAPH_ID,
} from "@openswe/shared/constants";
import type { DesignGraphState } from "@openswe/shared/open-swe/design/types";
import type { PlannerGraphUpdate } from "@openswe/shared/open-swe/planner/types";
import { coerceFeatureGraph } from "@/lib/coerce-feature-graph";
import {
  reconcileFeatureGraph,
  clarifyFeatureDescription,
} from "@openswe/shared/feature-graph";

function resolveApiUrl(): string {
  return (
    process.env.LANGGRAPH_API_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://localhost:2024"
  );
}

/**
 * POST /api/design/handoff
 *
 * Hands off features from a design thread to an isolated planner thread.
 * This creates a new planner thread to prevent "thread busy" errors.
 *
 * Body:
 * - design_thread_id: string - The design thread to hand off from
 * - feature_ids?: string[] - Specific features to hand off (defaults to readyFeatureIds)
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  try {
    const body = await request.json();
    const designThreadId = typeof body?.design_thread_id === "string"
      ? body.design_thread_id.trim()
      : typeof body?.designThreadId === "string"
        ? body.designThreadId.trim()
        : undefined;

    const requestedFeatureIds = Array.isArray(body?.feature_ids)
      ? body.feature_ids.filter((id: unknown): id is string => typeof id === "string")
      : Array.isArray(body?.featureIds)
        ? body.featureIds.filter((id: unknown): id is string => typeof id === "string")
        : undefined;

    if (!designThreadId) {
      return NextResponse.json(
        { error: "design_thread_id is required" },
        { status: 400 },
      );
    }

    const client = new Client({
      apiUrl: resolveApiUrl(),
      defaultHeaders:
        process.env.OPEN_SWE_LOCAL_MODE === "true"
          ? { [LOCAL_MODE_HEADER]: "true" }
          : undefined,
    });

    // Get the design thread state
    const designThreadState =
      await client.threads.getState<DesignGraphState>(designThreadId);

    if (!designThreadState?.values) {
      return NextResponse.json(
        { error: "Design thread state not found" },
        { status: 404 },
      );
    }

    const designState = designThreadState.values;
    const featureGraph = coerceFeatureGraph(designState.featureGraph);

    if (!featureGraph) {
      return NextResponse.json(
        { error: "No feature graph available in design thread" },
        { status: 400 },
      );
    }

    // Determine which features to hand off
    const featureIdsToHandoff = requestedFeatureIds?.length
      ? requestedFeatureIds
      : designState.readyFeatureIds ?? [];

    if (featureIdsToHandoff.length === 0) {
      return NextResponse.json(
        { error: "No features specified or marked ready for development. Use mark_ready_for_development first." },
        { status: 400 },
      );
    }

    // Validate all requested features exist
    const invalidFeatureIds = featureIdsToHandoff.filter(
      (id: string) => !featureGraph.hasFeature(id)
    );

    if (invalidFeatureIds.length > 0) {
      return NextResponse.json(
        { error: `Features not found: ${invalidFeatureIds.join(", ")}` },
        { status: 400 },
      );
    }

    // Generate a new, isolated planner thread ID
    const plannerThreadId = randomUUID();

    // Reconcile the feature graph to resolve dependencies
    const { graph: reconciledGraph, dependencyMap } = reconcileFeatureGraph(featureGraph);

    // Collect feature details and dependencies
    const features = featureIdsToHandoff
      .map((id: string) => reconciledGraph.getFeature(id))
      .filter((f: any): f is NonNullable<typeof f> => f !== undefined);

    const allDependencyIds = new Set<string>();
    for (const featureId of featureIdsToHandoff) {
      const deps = dependencyMap[featureId] ?? [];
      deps.forEach((dep: string) => allDependencyIds.add(dep));
    }

    const featureDependencies = Array.from(allDependencyIds)
      .filter((id: string) => !featureIdsToHandoff.includes(id))
      .map((id: string) => reconciledGraph.getFeature(id))
      .filter((f: any): f is NonNullable<typeof f> => f !== undefined);

    // Build feature description for the primary feature
    const primaryFeature = features[0];
    const featureDescription = primaryFeature
      ? clarifyFeatureDescription(primaryFeature)
      : undefined;

    const plannerRunInput: PlannerGraphUpdate = {
      targetRepository: designState.targetRepository,
      taskPlan: {
        tasks: [],
        reasoning: `Design handoff for features: ${featureIdsToHandoff.join(", ")}`,
      },
      branchName: `design-${plannerThreadId.slice(0, 8)}`,
      workspacePath: designState.workspacePath,
      activeFeatureIds: featureIdsToHandoff,
      features,
      featureDependencies,
      featureDependencyMap: dependencyMap,
      featureDescription,
      messages: designState.messages?.slice(-5), // Include recent context
    };

    // Create the planner run
    const run = await client.runs.create(plannerThreadId, PLANNER_GRAPH_ID, {
      input: plannerRunInput,
      config: {
        recursion_limit: 400,
        configurable: {
          ...(process.env.OPEN_SWE_LOCAL_MODE === "true"
            ? { [LOCAL_MODE_HEADER]: "true" }
            : {}),
        },
      },
      ifNotExists: "create",
      streamResumable: true,
      streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
    });

    return NextResponse.json({
      planner_thread_id: plannerThreadId,
      run_id: run.run_id,
      design_thread_id: designThreadId,
      feature_ids: featureIdsToHandoff,
      feature_count: features.length,
      dependency_count: featureDependencies.length,
      status: "handed_off",
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to hand off to planner";
    console.error("Design handoff failed:", error);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
