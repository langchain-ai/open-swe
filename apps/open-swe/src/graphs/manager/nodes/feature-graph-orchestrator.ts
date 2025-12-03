import { Command, END } from "@langchain/langgraph";
import { Client } from "@langchain/langgraph-sdk";
import {
  AIMessage,
  BaseMessage,
  HumanMessage,
  SystemMessage,
  ToolMessage,
  isHumanMessage,
} from "@langchain/core/messages";
import { FeatureGraph } from "@openswe/shared/feature-graph";
import {
  FeatureEdge,
  FeatureNode,
  featureGraphFileSchema,
} from "@openswe/shared/feature-graph/types";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import {
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import { LOCAL_MODE_HEADER } from "@openswe/shared/constants";
import { getMessageContentString } from "@openswe/shared/messages";
import {
  featureGraphToFile,
  persistFeatureGraph,
} from "../utils/feature-graph-mutations.js";
import { createLogger, LogLevel } from "../../../utils/logger.js";

const FEATURE_PLANNER_AGENT_ID = "openswe-feature-planner";
const FEATURE_PLANNER_API_URL =
  process.env.OPEN_SWE_FEATURE_PLANNER_URL ??
  "http://localhost/openswe-feature-planner";

const logger = createLogger(LogLevel.INFO, "FeatureGraphOrchestrator");

const normalizeFeatureIds = (
  value: string[] | undefined,
): string[] | undefined => {
  if (!Array.isArray(value)) return undefined;

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

  return normalized.length > 0 ? normalized : undefined;
};

const isFeatureEdge = (candidate: unknown): candidate is FeatureEdge => {
  if (!candidate || typeof candidate !== "object") return false;
  const edge = candidate as FeatureEdge;
  return (
    typeof edge.source === "string" &&
    typeof edge.target === "string" &&
    typeof edge.type === "string"
  );
};

const coerceFeatureGraph = (value: unknown): FeatureGraph | null => {
  if (!value) return null;
  if (value instanceof FeatureGraph) return value;

  const payload =
    typeof value === "object" && value !== null && "data" in value
      ? (value as { data?: unknown }).data ?? value
      : value;

  const parsed = featureGraphFileSchema.safeParse(payload);
  if (!parsed.success) return null;

  const nodes = parsed.data.nodes.reduce((map, entry) => {
    if (!entry || typeof entry !== "object") return map;
    const node = entry as FeatureNode;
    if (typeof node.id === "string") {
      map.set(node.id, node);
    }
    return map;
  }, new Map<string, FeatureNode>());

  if (!nodes.size) return null;

  const edges = parsed.data.edges.filter(isFeatureEdge);

  try {
    return new FeatureGraph({
      version: parsed.data.version,
      nodes,
      edges,
      artifacts: parsed.data.artifacts,
    });
  } catch (error) {
    logger.error("Failed to coerce feature graph from planner output", {
      error: error instanceof Error ? error.message : String(error),
    });
    return null;
  }
};

const graphChanged = (existing: FeatureGraph, incoming: FeatureGraph): boolean => {
  try {
    const currentSerialized = JSON.stringify(featureGraphToFile(existing));
    const incomingSerialized = JSON.stringify(featureGraphToFile(incoming));
    return currentSerialized !== incomingSerialized;
  } catch (error) {
    logger.warn("Unable to compare feature graphs", {
      error: error instanceof Error ? error.message : String(error),
    });
    return true;
  }
};

const coerceMessage = (value: unknown): BaseMessage | null => {
  if (value instanceof HumanMessage) return value;
  if (value instanceof AIMessage) return value;
  if (value instanceof SystemMessage) return value;
  if (value instanceof ToolMessage) return value;

  if (!value || typeof value !== "object") return null;
  const candidate = value as {
    type?: string;
    content?: unknown;
    name?: string;
    tool_call_id?: string;
    tool_calls?: unknown;
    additional_kwargs?: Record<string, unknown>;
  };

  if (typeof candidate.content !== "string") return null;

  switch (candidate.type) {
    case "human":
      return new HumanMessage({
        content: candidate.content,
        name: candidate.name,
        additional_kwargs: candidate.additional_kwargs,
      });
    case "system":
      return new SystemMessage({
        content: candidate.content,
        name: candidate.name,
        additional_kwargs: candidate.additional_kwargs,
      });
    case "tool":
      if (!candidate.tool_call_id) return null;
      return new ToolMessage({
        content: candidate.content,
        name: candidate.name,
        tool_call_id: candidate.tool_call_id,
        additional_kwargs: candidate.additional_kwargs,
      });
    default:
      return new AIMessage({
        content: candidate.content,
        name: candidate.name,
        tool_calls: candidate.tool_calls as AIMessage["tool_calls"],
        additional_kwargs: candidate.additional_kwargs,
      });
  }
};

const coerceMessages = (value: unknown): BaseMessage[] => {
  if (!Array.isArray(value)) return [];
  return value
    .map((entry) => coerceMessage(entry))
    .filter((entry): entry is BaseMessage => Boolean(entry));
};

const buildPlannerClient = (): Client =>
  new Client({
    apiUrl: FEATURE_PLANNER_API_URL,
    defaultHeaders:
      process.env.OPEN_SWE_LOCAL_MODE === "true"
        ? { [LOCAL_MODE_HEADER]: "true" }
        : undefined,
  });

export type FeaturePlannerValues = {
  messages?: unknown;
  featureGraph?: unknown;
  activeFeatureIds?: unknown;
  response?: unknown;
};

export async function featureGraphOrchestrator(
  state: ManagerGraphState,
  config: GraphConfig,
): Promise<Command> {
  const userMessage = state.messages.findLast(isHumanMessage);
  if (!userMessage || !state.featureGraph) {
    return new Command({
      goto: "classify-message",
    });
  }

  const plannerClient = buildPlannerClient();

  let plannerValues: FeaturePlannerValues | null = null;
  try {
    const plannerResult = await plannerClient.runs.wait(
      null,
      FEATURE_PLANNER_AGENT_ID,
      {
        input: {
          messages: state.messages,
          featureGraph: featureGraphToFile(state.featureGraph),
          activeFeatureIds: state.activeFeatureIds,
          workspacePath: state.workspacePath,
          userMessage: getMessageContentString(userMessage.content),
        },
        config: {
          configurable: {
            ...(config.configurable ?? {}),
            ...(state.workspacePath ? { workspacePath: state.workspacePath } : {}),
          },
        },
        ifNotExists: "create",
      },
    );

    plannerValues = plannerResult as FeaturePlannerValues;
  } catch (error) {
    logger.error("Feature planner agent failed", {
      error: error instanceof Error ? error.message : String(error),
    });

    return new Command({
      goto: "classify-message",
    });
  }

  if (!plannerValues) {
    logger.warn("Feature planner agent returned no state");
    return new Command({
      goto: "classify-message",
    });
  }

  const updates: ManagerGraphUpdate = {};

  const agentMessages = coerceMessages(plannerValues?.messages);
  const responseMessages = coerceMessages(
    plannerValues?.response ? [plannerValues.response] : [],
  );
  const messages = [...agentMessages, ...responseMessages];

  let updatedGraph = coerceFeatureGraph(plannerValues?.featureGraph);
  const normalizedFeatureIds = normalizeFeatureIds(
    plannerValues?.activeFeatureIds as string[] | undefined,
  );

  let shouldEnd = Boolean(messages.length);

  if (updatedGraph) {
    const changed = graphChanged(state.featureGraph, updatedGraph);
    updates.featureGraph = updatedGraph;
    shouldEnd = shouldEnd || changed;

    if (changed) {
      await persistFeatureGraph(updatedGraph, state.workspacePath);
      logger.info("Persisted feature planner graph changes");
    }
  } else {
    updatedGraph = state.featureGraph;
  }

  if (normalizedFeatureIds) {
    updates.activeFeatureIds = normalizedFeatureIds;
  }

  if (messages.length) {
    updates.messages = messages;
  }

  return new Command({
    update: updates,
    goto: shouldEnd ? END : "classify-message",
  });
}
