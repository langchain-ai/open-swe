import { v4 as uuidv4 } from "uuid";
import { Command, END } from "@langchain/langgraph";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import { isLocalMode } from "@openswe/shared/open-swe/local-mode";
import {
  DesignGraphState,
  DesignGraphUpdate,
  DesignHandoffResult,
} from "@openswe/shared/open-swe/design/types";
import { PlannerGraphUpdate } from "@openswe/shared/open-swe/planner/types";
import { createLangGraphClient } from "../../../utils/langgraph-client.js";
import {
  OPEN_SWE_STREAM_MODE,
  PLANNER_GRAPH_ID,
  LOCAL_MODE_HEADER,
} from "@openswe/shared/constants";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { getCustomConfigurableFields } from "@openswe/shared/open-swe/utils/config";
import {
  reconcileFeatureGraph,
  clarifyFeatureDescription,
} from "@openswe/shared/feature-graph";
import { StreamMode } from "@langchain/langgraph-sdk";
import { AIMessage } from "@langchain/core/messages";

const logger = createLogger(LogLevel.INFO, "HandoffToPlanner");

/**
 * Handoff node that creates an isolated planner thread for development.
 * This ensures the design thread remains available for continued iteration
 * while development proceeds in a separate context.
 */
export async function handoffToPlanner(
  state: DesignGraphState,
  config: GraphConfig,
): Promise<Command> {
  const { featureGraph, readyFeatureIds, workspacePath, targetRepository } = state;

  if (!featureGraph) {
    const errorMessage = "Cannot hand off to planner: no feature graph available.";
    logger.error(errorMessage);

    return new Command({
      update: {
        messages: [new AIMessage({ content: errorMessage })],
      } satisfies DesignGraphUpdate,
      goto: END,
    });
  }

  const featureIdsToHandoff = readyFeatureIds ?? [];

  if (featureIdsToHandoff.length === 0) {
    const errorMessage = "Cannot hand off to planner: no features marked as ready for development. Use 'mark_ready_for_development' to select features first.";
    logger.error(errorMessage);

    return new Command({
      update: {
        messages: [new AIMessage({ content: errorMessage })],
      } satisfies DesignGraphUpdate,
      goto: END,
    });
  }

  const localMode = isLocalMode(config);
  const defaultHeaders: Record<string, string> = localMode
    ? { [LOCAL_MODE_HEADER]: "true" }
    : {};

  try {
    const langGraphClient = createLangGraphClient({
      defaultHeaders,
    });

    // Generate a new, isolated planner thread ID
    const plannerThreadId = uuidv4();

    // Reconcile the feature graph to resolve dependencies
    const { graph: reconciledGraph, dependencyMap } = reconcileFeatureGraph(featureGraph);

    // Collect feature details and dependencies
    const features = featureIdsToHandoff
      .map(id => reconciledGraph.getFeature(id))
      .filter((f): f is NonNullable<typeof f> => f !== undefined);

    const allDependencyIds = new Set<string>();
    for (const featureId of featureIdsToHandoff) {
      const deps = dependencyMap[featureId] ?? [];
      deps.forEach(dep => allDependencyIds.add(dep));
    }

    const featureDependencies = Array.from(allDependencyIds)
      .filter(id => !featureIdsToHandoff.includes(id))
      .map(id => reconciledGraph.getFeature(id))
      .filter((f): f is NonNullable<typeof f> => f !== undefined);

    // Build feature description for the primary feature
    const primaryFeature = features[0];
    const featureDescription = primaryFeature
      ? clarifyFeatureDescription(primaryFeature)
      : undefined;

    const plannerRunInput: PlannerGraphUpdate = {
      targetRepository,
      taskPlan: {
        tasks: [],
        reasoning: `Design handoff for features: ${featureIdsToHandoff.join(", ")}`,
      },
      branchName: `design-${plannerThreadId.slice(0, 8)}`,
      workspacePath,
      activeFeatureIds: featureIdsToHandoff,
      features,
      featureDependencies,
      featureDependencyMap: dependencyMap,
      featureDescription,
      messages: state.messages.slice(-5), // Include recent context
    };

    const configurableFields = getCustomConfigurableFields(config);

    const run = await langGraphClient.runs.create(
      plannerThreadId,
      PLANNER_GRAPH_ID,
      {
        input: plannerRunInput,
        config: {
          recursion_limit: 400,
          configurable: {
            ...configurableFields,
            ...(localMode && { [LOCAL_MODE_HEADER]: "true" }),
          },
        },
        ifNotExists: "create",
        streamResumable: true,
        streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
      },
    );

    logger.info("Created isolated planner thread", {
      plannerThreadId,
      runId: run.run_id,
      featureIds: featureIdsToHandoff,
      dependencyCount: featureDependencies.length,
    });

    const handoffResult: DesignHandoffResult = {
      plannerThreadId,
      runId: run.run_id,
      featureIds: featureIdsToHandoff,
      featureGraph: reconciledGraph,
    };

    const successMessage = `Successfully handed off ${featureIdsToHandoff.length} feature(s) to planner.

**Planner Thread ID**: ${plannerThreadId}
**Run ID**: ${run.run_id}
**Features**: ${features.map(f => f.name).join(", ")}
${featureDependencies.length > 0 ? `**Dependencies included**: ${featureDependencies.map(f => f.name).join(", ")}` : ""}

The planner is now working on these features in an isolated thread. You can:
1. Continue designing more features in this thread
2. Check the Planner tab to monitor development progress
3. Make additional changes that won't affect the in-progress development`;

    return new Command({
      update: {
        messages: [new AIMessage({ content: successMessage })],
        designSession: {
          ...state.designSession,
          phase: "refining",
          lastActivity: new Date().toISOString(),
          conversationSummary: `Handed off features to planner: ${featureIdsToHandoff.join(", ")}`,
        },
        // Keep readyFeatureIds so we track what was handed off
        changeHistory: [
          ...(state.changeHistory ?? []),
          {
            proposalId: uuidv4(),
            action: "handoff_to_planner",
            timestamp: new Date().toISOString(),
            summary: `Handed off to planner (Thread: ${plannerThreadId}): ${featureIdsToHandoff.join(", ")}`,
          },
        ],
      } satisfies DesignGraphUpdate,
      goto: END,
    });
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : "Failed to create planner thread";
    logger.error("Handoff to planner failed", {
      error: errorMessage,
      featureIds: featureIdsToHandoff,
    });

    return new Command({
      update: {
        messages: [new AIMessage({
          content: `Failed to hand off to planner: ${errorMessage}\n\nPlease try again or check that all required configuration is available.`,
        })],
      } satisfies DesignGraphUpdate,
      goto: END,
    });
  }
}
