import { randomUUID } from "node:crypto";
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
  persistFeatureGraph,
} from "../utils/feature-graph-mutations.js";
import { FeatureGraph } from "@openswe/shared/feature-graph";
import { createLogger, LogLevel } from "../../../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "FeatureGraphAgent");

const FEATURE_AGENT_SYSTEM_PROMPT = `You are the dedicated feature-graph concierge for Open SWE.
- Maintain an explicit propose/approve/reject loop with the user instead of jumping into planning.
- Persist proposal state across turns so the user can approve or reject later.
- Only mutate the feature graph through the provided tools; summarize every mutation in your response.
- When proposing, explain the next approval step. When approving or rejecting, confirm the status change.
- If the feature graph is missing, ask for the workspace to be resolved or a graph to be generated.`;

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
  const systemPrompt = `${FEATURE_AGENT_SYSTEM_PROMPT}\n\n# Current Proposals\n${formatProposals(proposalState)}\n\n# Feature Graph\n${formatFeatureCatalog(state.featureGraph, state.activeFeatureIds)}`;

  const tools = [
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

  let updatedGraph = state.featureGraph;
  let updatedProposals = proposalState;
  const toolMessages: BaseMessage[] = [];
  const userFacingSummaries: string[] = [];

  for (const toolCall of aiMessage.tool_calls ?? []) {
    try {
      switch (toolCall.name) {
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

          if (updatedGraph) {
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

          const response =
            args.response ||
            `Proposed update for ${args.featureId}. Awaiting your approval.`;
          toolMessages.push(
            recordAction(toolCall.name, toolCall.id, response),
          );
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

          const response = args.response ||
            `Marked ${args.featureId} as approved and ready for planning.`;
          toolMessages.push(
            recordAction(toolCall.name, toolCall.id, response),
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
          toolMessages.push(
            recordAction(toolCall.name, toolCall.id, response),
          );
          userFacingSummaries.push(response);
          break;
        }
        case "reply_without_change": {
          const args = toolCall.args as z.infer<typeof replySchema>;
          toolMessages.push(
            recordAction(toolCall.name, toolCall.id, args.response),
          );
          userFacingSummaries.push(args.response);
          break;
        }
        default: {
          toolMessages.push(
            recordAction(
              toolCall.name,
              toolCall.id,
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
          toolCall.id,
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
    workspacePath: state.workspacePath,
    ...(updatedGraph ? { featureGraph: updatedGraph } : {}),
  };

  return new Command({
    update: updates,
    goto: END,
  });
}
