import { randomUUID } from "node:crypto";
import path from "node:path";
import { Command, END } from "@langchain/langgraph";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import {
  FeatureProposal,
  FeatureProposalState,
  ManagerGraphState,
  ManagerGraphUpdate,
} from "@openswe/shared/open-swe/manager/types";
import { loadModel, supportsParallelToolCallsParam } from "../../../utils/llms/index.js";
import { LLMTask } from "@openswe/shared/open-swe/llm-task";
import {
  AIMessage,
  BaseMessage,
  ToolMessage,
  isHumanMessage,
} from "@langchain/core/messages";
import { z } from "zod";
import { getMessageContentString } from "@openswe/shared/messages";
import {
  applyFeatureStatus,
  createFeatureNode,
  persistFeatureGraph,
} from "../utils/feature-graph-mutations.js";
import { FeatureGraph, loadFeatureGraph } from "@openswe/shared/feature-graph";
import type { FeatureNode } from "@openswe/shared/feature-graph/types";
import type { FeatureGraphJson } from "@openswe/shared";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { FEATURE_GRAPH_RELATIVE_PATH } from "../utils/feature-graph-path.js";

const logger = createLogger(LogLevel.INFO, "FeatureGraphAgent");

const FEATURE_AGENT_SYSTEM_PROMPT = `You are the dedicated feature-graph concierge for Open SWE.
- Maintain an explicit propose/approve/reject loop with the user instead of jumping into planning.
- Persist proposal state across turns so the user can approve or reject later.
- Only mutate the feature graph through the provided tools; summarize every mutation in your response.
- Use the create_feature tool to add new features before proposing updates for them.
- When proposing, explain the next approval step. When approving or rejecting, confirm the status change.
- If the feature graph is missing, ask for the workspace to be resolved or a graph to be generated.`;

const createFeatureSchema = z.object({
  featureId: z.string(),
  name: z.string(),
  summary: z.string(),
});

const proposeSchema = z.object({
  featureId: z.string(),
  summary: z.string(),
  rationale: z.string().optional(),
  response: z
    .string()
    .describe(
      "A concise user-facing update describing the proposal and the approval you need next.",
    ),
});

const approveSchema = z.object({
  featureId: z.string(),
  proposalId: z.string().optional(),
  rationale: z.string().optional(),
  response: z
    .string()
    .describe("A concise user-facing confirmation that the proposal is approved."),
});

const rejectSchema = z.object({
  featureId: z.string(),
  proposalId: z.string().optional(),
  rationale: z.string().optional(),
  response: z
    .string()
    .describe("A concise user-facing confirmation that the proposal is rejected."),
});

const replySchema = z.object({
  response: z
    .string()
    .describe(
      "A concise update to the user when no feature-graph mutation is required.",
    ),
});

const ensureProposalState = (
  state: FeatureProposalState | undefined,
): FeatureProposalState => state ?? { proposals: [] };

const initializeFeatureGraph = async (
  workspacePath: string | undefined,
): Promise<FeatureGraph | undefined> => {
  if (!workspacePath) return undefined;

  const graphPath = path.join(workspacePath, FEATURE_GRAPH_RELATIVE_PATH);

  try {
    const data = await loadFeatureGraph(graphPath);
    logger.info("Loaded feature graph from disk", { graphPath });
    return new FeatureGraph(data);
  } catch (error) {
    logger.warn("Falling back to an empty feature graph", {
      graphPath,
      error: error instanceof Error ? error.message : String(error),
    });

    const emptyGraph = new FeatureGraph({
      version: 1,
      nodes: new Map(),
      edges: [],
      artifacts: [],
    });

    await persistFeatureGraph(emptyGraph, workspacePath);

    return emptyGraph;
  }
};

const formatProposals = (state: FeatureProposalState): string => {
  if (!state.proposals.length) {
    return "No recorded proposals yet.";
  }

  return state.proposals
    .map((proposal) => {
      const status = proposal.status.toUpperCase();
      const rationale = proposal.rationale ? ` — ${proposal.rationale}` : "";
      return `${proposal.featureId}: ${proposal.summary} [${status}]${rationale}`;
    })
    .join("\n");
};

const formatFeatureCatalog = (
  featureGraph: FeatureGraph | undefined,
  activeFeatureIds: string[] | undefined,
): string => {
  if (!featureGraph) return "No feature graph available.";

  const activeIds = new Set(activeFeatureIds ?? []);
  return featureGraph
    .listFeatures()
    .map((feature) => {
      const activeMarker = activeIds.has(feature.id) ? "(active)" : "";
      return `- ${feature.id} ${activeMarker}: ${feature.name} — ${feature.status}`;
    })
    .join("\n");
};

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

const deserializeFeatureGraph = (
  graph: FeatureGraphJson | undefined,
): FeatureGraph | undefined => {
  if (!graph) return undefined;

  const nodes = new Map<string, FeatureNode>();

  for (const entry of graph.nodes) {
    if (Array.isArray(entry) && entry.length >= 2) {
      const [id, node] = entry;
      if (typeof id === "string" && node && typeof node === "object") {
        nodes.set(id, node as FeatureNode);
      }
      continue;
    }

    if (entry && typeof entry === "object" && "id" in entry) {
      const node = entry as FeatureNode;
      if (typeof node.id === "string") {
        nodes.set(node.id, node);
      }
    }
  }

  if (!nodes.size) return undefined;

  const version = typeof graph.version === "number" ? graph.version : 1;

  try {
    return new FeatureGraph({
      version,
      nodes,
      edges: Array.isArray(graph.edges) ? graph.edges : [],
      artifacts: graph.artifacts,
    });
  } catch {
    return undefined;
  }
};

const mergeActiveFeatureIds = (
  nextIds: string | string[] | undefined,
  existingIds: string[] | undefined,
): string[] | undefined => {
  const combined = [
    ...(Array.isArray(nextIds) ? nextIds : nextIds ? [nextIds] : []),
    ...(existingIds ?? []),
  ];

  return normalizeFeatureIds(combined);
};

const upsertProposal = (
  state: FeatureProposalState,
  proposal: FeatureProposal,
): FeatureProposalState => {
  const proposals = state.proposals.filter(
    (existing) => existing.proposalId !== proposal.proposalId,
  );
  proposals.push(proposal);

  return {
    proposals,
    activeProposalId: proposal.proposalId,
  };
};

const recordAction = (
  toolName: string,
  toolCallId: string,
  content: string,
): ToolMessage =>
  new ToolMessage({
    content,
    tool_call_id: toolCallId,
    name: toolName,
  });

const nowIso = () => new Date().toISOString();

export async function featureGraphAgent(
  state: ManagerGraphState,
  config: GraphConfig,
): Promise<Command> {
  const userMessage = state.messages.findLast(isHumanMessage);

  if (!userMessage) {
    throw new Error("No human message found.");
  }

  const proposalState = ensureProposalState(state.featureProposals);
  const featureGraph = deserializeFeatureGraph(state.featureGraph);
  const systemPrompt = `${FEATURE_AGENT_SYSTEM_PROMPT}\n\n# Current Proposals\n${formatProposals(proposalState)}\n\n# Feature Graph\n${formatFeatureCatalog(featureGraph, state.activeFeatureIds)}`;

  const tools = [
    {
      name: "create_feature",
      description: "Add a new feature node to the feature graph before proposing changes.",
      schema: createFeatureSchema,
    },
    {
      name: "propose_feature_change",
      description:
        "Propose a new or updated feature definition in the graph and request approval.",
      schema: proposeSchema,
    },
    {
      name: "approve_feature_change",
      description: "Mark a pending proposal as approved and activate the feature.",
      schema: approveSchema,
    },
    {
      name: "reject_feature_change",
      description: "Reject a pending proposal and record the rationale.",
      schema: rejectSchema,
    },
    {
      name: "reply_without_change",
      description:
        "Respond to the user without mutating the feature graph when more info is needed.",
      schema: replySchema,
    },
  ];

  const model = await loadModel(config, LLMTask.ROUTER);
  const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
    config,
    LLMTask.ROUTER,
  );
  const modelWithTools = model.bindTools(tools, {
    tool_choice: "auto",
    ...(modelSupportsParallelToolCallsParam
      ? { parallel_tool_calls: false }
      : {}),
  });

  const aiMessage = await modelWithTools.invoke([
    { role: "system", content: systemPrompt },
    {
      role: "user",
      content: getMessageContentString(userMessage.content),
    },
  ]);

  let updatedGraph = featureGraph;
  let updatedProposals = proposalState;
  let updatedActiveFeatureIds = normalizeFeatureIds(state.activeFeatureIds);
  const toolMessages: BaseMessage[] = [];
  const userFacingSummaries: string[] = [];

  for (const toolCall of aiMessage.tool_calls ?? []) {
    const toolCallId = toolCall.id ?? randomUUID();

    try {
      switch (toolCall.name) {
        case "create_feature": {
          const args = toolCall.args as z.infer<typeof createFeatureSchema>;

          if (!updatedGraph) {
            updatedGraph = await initializeFeatureGraph(state.workspacePath);

            if (!updatedGraph) {
              throw new Error(
                "Workspace path is not set; cannot initialize feature graph.",
              );
            }
          }

          updatedGraph = await createFeatureNode(
            updatedGraph,
            {
              id: args.featureId,
              name: args.name,
              summary: args.summary,
            },
            state.workspacePath,
          );

          const response = `Added ${args.name} (${args.featureId}) to the feature graph.`;
          toolMessages.push(recordAction(toolCall.name, toolCallId, response));
          userFacingSummaries.push(response);
          break;
        }
        case "propose_feature_change": {
          const args = toolCall.args as z.infer<typeof proposeSchema>;
          const proposalId = randomUUID();
          const proposal: FeatureProposal = {
            proposalId,
            featureId: args.featureId,
            summary: args.summary,
            status: "proposed",
            rationale: args.rationale,
            updatedAt: nowIso(),
          };
          updatedProposals = upsertProposal(updatedProposals, proposal);

          logger.info("Recorded feature proposal", {
            action: toolCall.name,
            featureId: args.featureId,
            proposalId,
            status: proposal.status,
          });

          let creationSummary: string | undefined;

          if (!updatedGraph) {
            updatedGraph = await initializeFeatureGraph(state.workspacePath);
          }

          if (updatedGraph) {
            if (!updatedGraph.hasFeature(args.featureId)) {
              updatedGraph = await createFeatureNode(
                updatedGraph,
                {
                  id: args.featureId,
                  name: args.featureId,
                  summary: args.summary,
                },
                state.workspacePath,
              );
              creationSummary = `Initialized ${args.featureId} in the feature graph.`;
            }

            updatedGraph = applyFeatureStatus(
              updatedGraph,
              args.featureId,
              "proposed",
            );
            await persistFeatureGraph(updatedGraph, state.workspacePath);

            logger.info("Updated feature graph status", {
              featureId: args.featureId,
              proposalId,
              status: "proposed",
            });
          }

          const response = [
            creationSummary,
            args.response ||
              `Proposed update for ${args.featureId}. Awaiting your approval.`,
          ]
            .filter(Boolean)
            .join(" ");
          toolMessages.push(recordAction(toolCall.name, toolCallId, response));
          userFacingSummaries.push(response);
          break;
        }
        case "approve_feature_change": {
          const args = toolCall.args as z.infer<typeof approveSchema>;
          const matchingProposal = updatedProposals.proposals.find(
            (proposal) =>
              proposal.proposalId === args.proposalId ||
              proposal.featureId === args.featureId,
          );
          const proposal: FeatureProposal = {
            proposalId: matchingProposal?.proposalId ?? randomUUID(),
            featureId: args.featureId,
            summary:
              matchingProposal?.summary ??
              `Approved update for ${args.featureId}`,
            status: "approved",
            rationale: args.rationale,
            updatedAt: nowIso(),
          };
          updatedProposals = upsertProposal(updatedProposals, proposal);

          logger.info("Approved feature proposal", {
            action: toolCall.name,
            featureId: args.featureId,
            proposalId: proposal.proposalId,
            status: proposal.status,
          });

          if (updatedGraph) {
            updatedGraph = applyFeatureStatus(
              updatedGraph,
              args.featureId,
              "active",
            );
            await persistFeatureGraph(updatedGraph, state.workspacePath);

            logger.info("Activated feature in graph", {
              featureId: args.featureId,
              proposalId: proposal.proposalId,
              status: "active",
            });
          }

          updatedActiveFeatureIds = mergeActiveFeatureIds(
            args.featureId,
            updatedActiveFeatureIds ?? state.activeFeatureIds,
          );

          const response = args.response ||
            `Marked ${args.featureId} as approved and ready for planning.`;
          toolMessages.push(
            recordAction(toolCall.name, toolCallId, response),
          );
          userFacingSummaries.push(response);
          break;
        }
        case "reject_feature_change": {
          const args = toolCall.args as z.infer<typeof rejectSchema>;
          const matchingProposal = updatedProposals.proposals.find(
            (proposal) =>
              proposal.proposalId === args.proposalId ||
              proposal.featureId === args.featureId,
          );
          const proposal: FeatureProposal = {
            proposalId: matchingProposal?.proposalId ?? randomUUID(),
            featureId: args.featureId,
            summary:
              matchingProposal?.summary ?? `Rejected update for ${args.featureId}`,
            status: "rejected",
            rationale: args.rationale,
            updatedAt: nowIso(),
          };
          updatedProposals = upsertProposal(updatedProposals, proposal);

          logger.info("Rejected feature proposal", {
            action: toolCall.name,
            featureId: args.featureId,
            proposalId: proposal.proposalId,
            status: proposal.status,
          });

          if (updatedGraph) {
            updatedGraph = applyFeatureStatus(
              updatedGraph,
              args.featureId,
              "rejected",
            );
            await persistFeatureGraph(updatedGraph, state.workspacePath);

            logger.info("Updated rejected feature in graph", {
              featureId: args.featureId,
              proposalId: proposal.proposalId,
              status: "rejected",
            });
          }

          const response =
            args.response ||
            `Logged rejection for ${args.featureId} so we do not plan against it.`;
          toolMessages.push(recordAction(toolCall.name, toolCallId, response));
          userFacingSummaries.push(response);
          break;
        }
        case "reply_without_change": {
          const args = toolCall.args as z.infer<typeof replySchema>;
          toolMessages.push(
            recordAction(toolCall.name, toolCallId, args.response),
          );
          userFacingSummaries.push(args.response);
          break;
        }
        default: {
          toolMessages.push(
            recordAction(
              toolCall.name,
              toolCallId,
              `Unsupported action ${toolCall.name}.`,
            ),
          );
        }
      }
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : String(error ?? "Unknown error");
      const toolArgs = toolCall.args as Record<string, unknown> | undefined;
      logger.error("Failed to process feature graph action", {
        action: toolCall.name,
        featureId:
          toolArgs && typeof toolArgs.featureId === "string"
            ? toolArgs.featureId
            : undefined,
        proposalId:
          toolArgs && typeof toolArgs.proposalId === "string"
            ? toolArgs.proposalId
            : undefined,
        error: errorMessage,
      });
      toolMessages.push(
        recordAction(
          toolCall.name,
          toolCallId,
          `Could not process ${toolCall.name}: ${errorMessage}`,
        ),
      );
      userFacingSummaries.push(
        `I couldn't complete ${toolCall.name}. Please restate the feature and desired status.`,
      );
    }
  }

  const combinedContent = [
    getMessageContentString(aiMessage.content),
    ...userFacingSummaries,
  ]
    .map((entry) => entry?.trim())
    .filter((entry): entry is string => Boolean(entry))
    .join("\n\n");

  const responseMessage = combinedContent
    ? new AIMessage({ content: combinedContent })
    : undefined;

  const updates: ManagerGraphUpdate = {
    messages: [aiMessage, ...toolMessages, ...(responseMessage ? [responseMessage] : [])],
    featureProposals: updatedProposals,
    ...(updatedGraph ? { featureGraph: updatedGraph.toJSON() } : {}),
    ...(updatedActiveFeatureIds
      ? { activeFeatureIds: updatedActiveFeatureIds }
      : {}),
  };

  return new Command({
    update: updates,
    goto: END,
  });
}
