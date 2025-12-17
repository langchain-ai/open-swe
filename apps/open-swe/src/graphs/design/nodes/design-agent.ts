import { randomUUID } from "node:crypto";
import path from "node:path";
import { Command, END, interrupt } from "@langchain/langgraph";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import {
  DesignGraphState,
  DesignGraphUpdate,
  FeatureChangeProposal,
  ClarifyingQuestion,
} from "@openswe/shared/open-swe/design/types";
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
  FeatureGraph,
  listFeaturesFromGraph,
  loadFeatureGraph,
} from "@openswe/shared/feature-graph";
import type { FeatureNode, FeatureEdge } from "@openswe/shared/feature-graph/types";
import { createLogger, LogLevel } from "../../../utils/logger.js";
import { FEATURE_GRAPH_RELATIVE_PATH } from "../../manager/utils/feature-graph-path.js";
import { persistFeatureGraph } from "../../manager/utils/feature-graph-mutations.js";

const logger = createLogger(LogLevel.INFO, "DesignAgent");

const DESIGN_AGENT_SYSTEM_PROMPT = `You are a Feature Graph Design Assistant for Open SWE.
Your role is to have a collaborative, iterative conversation with the user to design and refine the feature graph.

## Core Principles

1. **Conversational Design**: Engage in back-and-forth dialogue to understand requirements before making changes.
2. **Impact Awareness**: Always analyze how changes to one feature might affect others.
3. **Clarifying Questions**: Ask questions when requirements are ambiguous or when a change could have unintended consequences.
4. **Incremental Changes**: Propose changes one at a time when possible, explaining the rationale.
5. **Dependency Awareness**: When creating or modifying features, consider and explain their relationships.

## Workflow

1. **Understand**: First understand what the user wants to achieve.
2. **Analyze**: Analyze the current feature graph state and identify impacts.
3. **Clarify**: If anything is unclear or there are potential conflicts, ask clarifying questions.
4. **Propose**: Propose specific changes with clear rationale.
5. **Confirm**: Wait for user approval before applying changes.

## Feature States

- **inactive**: Not yet started
- **proposed**: Under consideration
- **active**: Approved and ready for development
- **rejected**: Not moving forward

## Available Actions

- Create new features with proper relationships
- Update existing feature details
- Connect features (create dependencies)
- Disconnect features (remove dependencies)
- Mark features as ready for development
- Ask clarifying questions about requirements or impacts

Remember: The goal is to create a well-structured feature graph through collaborative design, not to rush changes.`;

// Tool schemas
const createFeatureSchema = z.object({
  featureId: z.string().describe("Unique identifier for the feature"),
  name: z.string().describe("Human-readable feature name"),
  description: z.string().describe("Detailed description of what the feature does"),
  group: z.string().optional().describe("Optional grouping for related features"),
  rationale: z.string().describe("Why this feature should be added"),
});

const updateFeatureSchema = z.object({
  featureId: z.string().describe("ID of the feature to update"),
  name: z.string().optional().describe("New name for the feature"),
  description: z.string().optional().describe("New description"),
  group: z.string().optional().describe("New group assignment"),
  rationale: z.string().describe("Why this update is needed"),
});

const connectFeaturesSchema = z.object({
  sourceFeatureId: z.string().describe("The feature that depends on another"),
  targetFeatureId: z.string().describe("The feature being depended upon"),
  connectionType: z.enum(["depends_on", "extends", "related_to"]).describe("Type of relationship"),
  rationale: z.string().describe("Why these features should be connected"),
});

const disconnectFeaturesSchema = z.object({
  sourceFeatureId: z.string().describe("Source feature of the connection"),
  targetFeatureId: z.string().describe("Target feature of the connection"),
  rationale: z.string().describe("Why this connection should be removed"),
});

const markReadyForDevelopmentSchema = z.object({
  featureIds: z.array(z.string()).describe("Feature IDs to mark as ready"),
  rationale: z.string().describe("Why these features are ready for development"),
});

const askClarifyingQuestionSchema = z.object({
  question: z.string().describe("The question to ask the user"),
  context: z.string().describe("Why this question is important"),
  relatedFeatureIds: z.array(z.string()).optional().describe("Features related to this question"),
  options: z.array(z.string()).optional().describe("Suggested answers if applicable"),
});

const analyzeImpactSchema = z.object({
  featureId: z.string().describe("Feature to analyze impact for"),
  changeType: z.enum(["create", "update", "delete", "connect", "disconnect"]).describe("Type of change"),
  targetFeatureId: z.string().optional().describe("Target feature if applicable"),
});

const respondToUserSchema = z.object({
  response: z.string().describe("Response to the user without making changes"),
  nextSteps: z.string().optional().describe("Suggested next steps for the design process"),
});

const nowIso = () => new Date().toISOString();

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
    logger.warn("Creating empty feature graph", {
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

const formatFeatureGraph = (
  featureGraph: FeatureGraph | undefined,
  readyFeatureIds: string[] | undefined,
): string => {
  if (!featureGraph) return "No feature graph available yet.";

  const readyIds = new Set(readyFeatureIds ?? []);
  const features = listFeaturesFromGraph(featureGraph.toJSON());

  if (features.length === 0) {
    return "Feature graph is empty. Ready to design new features.";
  }

  const featureLines = features.map((feature) => {
    const readyMarker = readyIds.has(feature.id) ? " [READY]" : "";
    const group = feature.group ? ` (${feature.group})` : "";
    return `- ${feature.id}${readyMarker}${group}: ${feature.name} — ${feature.status}\n  ${feature.description}`;
  });

  const edges = featureGraph.toJSON().edges;
  const edgeLines = edges.length > 0
    ? "\n\nDependencies:\n" + edges.map(e => `- ${e.source} → ${e.target} (${e.type})`).join("\n")
    : "";

  return `Current Features:\n${featureLines.join("\n")}${edgeLines}`;
};

const formatPendingProposals = (proposals: FeatureChangeProposal[] | undefined): string => {
  if (!proposals || proposals.length === 0) {
    return "No pending proposals.";
  }

  return "Pending Proposals:\n" + proposals
    .filter(p => p.status === "pending")
    .map(p => `- [${p.id}] ${p.type} ${p.featureId}: ${p.summary}`)
    .join("\n");
};

const formatClarifyingQuestions = (questions: ClarifyingQuestion[] | undefined): string => {
  if (!questions || questions.length === 0) return "";

  const pending = questions.filter(q => q.status === "pending");
  if (pending.length === 0) return "";

  return "\n\nPending Questions:\n" + pending
    .map(q => `- [${q.id}] ${q.question}`)
    .join("\n");
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

export async function designAgent(
  state: DesignGraphState,
  config: GraphConfig,
): Promise<Command> {
  const userMessage = state.messages.findLast(isHumanMessage);

  if (!userMessage) {
    throw new Error("No human message found in design conversation.");
  }

  let featureGraph = state.featureGraph;
  if (!featureGraph && state.workspacePath) {
    featureGraph = await initializeFeatureGraph(state.workspacePath);
  }

  const systemPrompt = `${DESIGN_AGENT_SYSTEM_PROMPT}

# Current State

${formatFeatureGraph(featureGraph, state.readyFeatureIds)}

${formatPendingProposals(state.pendingProposals)}
${formatClarifyingQuestions(state.clarifyingQuestions)}

# Design Session
Phase: ${state.designSession?.phase ?? "exploring"}
${state.designSession?.designGoal ? `Goal: ${state.designSession.designGoal}` : ""}
${state.designSession?.conversationSummary ? `Summary: ${state.designSession.conversationSummary}` : ""}`;

  const tools = [
    {
      name: "create_feature",
      description: "Create a new feature node in the graph",
      schema: createFeatureSchema,
    },
    {
      name: "update_feature",
      description: "Update an existing feature's details",
      schema: updateFeatureSchema,
    },
    {
      name: "connect_features",
      description: "Create a dependency or relationship between two features",
      schema: connectFeaturesSchema,
    },
    {
      name: "disconnect_features",
      description: "Remove a connection between two features",
      schema: disconnectFeaturesSchema,
    },
    {
      name: "mark_ready_for_development",
      description: "Mark features as ready to hand off to the planner for development",
      schema: markReadyForDevelopmentSchema,
    },
    {
      name: "ask_clarifying_question",
      description: "Ask the user a clarifying question before proceeding with changes",
      schema: askClarifyingQuestionSchema,
    },
    {
      name: "analyze_impact",
      description: "Analyze the impact of a potential change on other features",
      schema: analyzeImpactSchema,
    },
    {
      name: "respond_to_user",
      description: "Respond to the user without making changes to the feature graph",
      schema: respondToUserSchema,
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
    ...state.messages.slice(-20), // Keep recent conversation context
    {
      role: "user",
      content: getMessageContentString(userMessage.content),
    },
  ]);

  let updatedGraph = featureGraph;
  let updatedProposals = [...(state.pendingProposals ?? [])];
  let updatedQuestions = [...(state.clarifyingQuestions ?? [])];
  let updatedReadyFeatureIds = [...(state.readyFeatureIds ?? [])];
  let updatedChangeHistory = [...(state.changeHistory ?? [])];
  let updatedImpactAnalysis = { ...(state.impactAnalysis ?? {}) };
  let updatedDesignSession = state.designSession ?? {
    phase: "exploring" as const,
    lastActivity: nowIso(),
  };

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
              throw new Error("Cannot create feature: workspace path not set.");
            }
          }

          const newFeature: FeatureNode = {
            id: args.featureId,
            name: args.name,
            description: args.description,
            status: "proposed",
            group: args.group,
          };

          const nodes = new Map(updatedGraph.toJSON().nodes.map(n => [n.id, n]));
          nodes.set(args.featureId, newFeature);

          updatedGraph = new FeatureGraph({
            version: updatedGraph.toJSON().version,
            nodes,
            edges: updatedGraph.toJSON().edges,
            artifacts: updatedGraph.toJSON().artifacts,
          });

          await persistFeatureGraph(updatedGraph, state.workspacePath);

          const proposal: FeatureChangeProposal = {
            id: randomUUID(),
            type: "create",
            featureId: args.featureId,
            summary: `Create feature: ${args.name}`,
            rationale: args.rationale,
            status: "approved",
            createdAt: nowIso(),
            updatedAt: nowIso(),
          };
          updatedProposals.push(proposal);

          updatedChangeHistory.push({
            proposalId: proposal.id,
            action: "create_feature",
            timestamp: nowIso(),
            summary: `Created feature ${args.featureId}: ${args.name}`,
          });

          updatedDesignSession = {
            ...updatedDesignSession,
            phase: "designing",
            lastActivity: nowIso(),
          };

          const response = `Created feature "${args.name}" (${args.featureId}).\nRationale: ${args.rationale}`;
          toolMessages.push(recordAction(toolCall.name, toolCallId, response));
          userFacingSummaries.push(response);
          break;
        }

        case "update_feature": {
          const args = toolCall.args as z.infer<typeof updateFeatureSchema>;

          if (!updatedGraph) {
            throw new Error("No feature graph available to update.");
          }

          const existingFeature = updatedGraph.getFeature(args.featureId);
          if (!existingFeature) {
            throw new Error(`Feature ${args.featureId} not found.`);
          }

          const updatedFeature: FeatureNode = {
            ...existingFeature,
            ...(args.name && { name: args.name }),
            ...(args.description && { description: args.description }),
            ...(args.group && { group: args.group }),
          };

          const nodes = new Map(updatedGraph.toJSON().nodes.map(n => [n.id, n]));
          nodes.set(args.featureId, updatedFeature);

          updatedGraph = new FeatureGraph({
            version: updatedGraph.toJSON().version,
            nodes,
            edges: updatedGraph.toJSON().edges,
            artifacts: updatedGraph.toJSON().artifacts,
          });

          await persistFeatureGraph(updatedGraph, state.workspacePath);

          updatedChangeHistory.push({
            proposalId: randomUUID(),
            action: "update_feature",
            timestamp: nowIso(),
            summary: `Updated feature ${args.featureId}`,
          });

          const response = `Updated feature "${args.featureId}".\nRationale: ${args.rationale}`;
          toolMessages.push(recordAction(toolCall.name, toolCallId, response));
          userFacingSummaries.push(response);
          break;
        }

        case "connect_features": {
          const args = toolCall.args as z.infer<typeof connectFeaturesSchema>;

          if (!updatedGraph) {
            throw new Error("No feature graph available.");
          }

          const sourceExists = updatedGraph.hasFeature(args.sourceFeatureId);
          const targetExists = updatedGraph.hasFeature(args.targetFeatureId);

          if (!sourceExists) {
            throw new Error(`Source feature ${args.sourceFeatureId} not found.`);
          }
          if (!targetExists) {
            throw new Error(`Target feature ${args.targetFeatureId} not found.`);
          }

          const existingEdges = updatedGraph.toJSON().edges;
          const alreadyConnected = existingEdges.some(
            e => e.source === args.sourceFeatureId && e.target === args.targetFeatureId
          );

          if (alreadyConnected) {
            const response = `Features ${args.sourceFeatureId} and ${args.targetFeatureId} are already connected.`;
            toolMessages.push(recordAction(toolCall.name, toolCallId, response));
            userFacingSummaries.push(response);
            break;
          }

          const newEdge: FeatureEdge = {
            source: args.sourceFeatureId,
            target: args.targetFeatureId,
            type: args.connectionType,
          };

          updatedGraph = new FeatureGraph({
            version: updatedGraph.toJSON().version,
            nodes: new Map(updatedGraph.toJSON().nodes.map(n => [n.id, n])),
            edges: [...existingEdges, newEdge],
            artifacts: updatedGraph.toJSON().artifacts,
          });

          await persistFeatureGraph(updatedGraph, state.workspacePath);

          updatedChangeHistory.push({
            proposalId: randomUUID(),
            action: "connect_features",
            timestamp: nowIso(),
            summary: `Connected ${args.sourceFeatureId} → ${args.targetFeatureId} (${args.connectionType})`,
          });

          const response = `Connected ${args.sourceFeatureId} → ${args.targetFeatureId} (${args.connectionType}).\nRationale: ${args.rationale}`;
          toolMessages.push(recordAction(toolCall.name, toolCallId, response));
          userFacingSummaries.push(response);
          break;
        }

        case "disconnect_features": {
          const args = toolCall.args as z.infer<typeof disconnectFeaturesSchema>;

          if (!updatedGraph) {
            throw new Error("No feature graph available.");
          }

          const existingEdges = updatedGraph.toJSON().edges;
          const filteredEdges = existingEdges.filter(
            e => !(e.source === args.sourceFeatureId && e.target === args.targetFeatureId)
          );

          if (filteredEdges.length === existingEdges.length) {
            const response = `No connection found between ${args.sourceFeatureId} and ${args.targetFeatureId}.`;
            toolMessages.push(recordAction(toolCall.name, toolCallId, response));
            userFacingSummaries.push(response);
            break;
          }

          updatedGraph = new FeatureGraph({
            version: updatedGraph.toJSON().version,
            nodes: new Map(updatedGraph.toJSON().nodes.map(n => [n.id, n])),
            edges: filteredEdges,
            artifacts: updatedGraph.toJSON().artifacts,
          });

          await persistFeatureGraph(updatedGraph, state.workspacePath);

          updatedChangeHistory.push({
            proposalId: randomUUID(),
            action: "disconnect_features",
            timestamp: nowIso(),
            summary: `Disconnected ${args.sourceFeatureId} from ${args.targetFeatureId}`,
          });

          const response = `Disconnected ${args.sourceFeatureId} from ${args.targetFeatureId}.\nRationale: ${args.rationale}`;
          toolMessages.push(recordAction(toolCall.name, toolCallId, response));
          userFacingSummaries.push(response);
          break;
        }

        case "mark_ready_for_development": {
          const args = toolCall.args as z.infer<typeof markReadyForDevelopmentSchema>;

          if (!updatedGraph) {
            throw new Error("No feature graph available.");
          }

          const validFeatureIds: string[] = [];
          const invalidFeatureIds: string[] = [];

          for (const featureId of args.featureIds) {
            if (updatedGraph.hasFeature(featureId)) {
              validFeatureIds.push(featureId);

              // Update feature status to active
              const feature = updatedGraph.getFeature(featureId);
              if (feature) {
                const nodes = new Map(updatedGraph.toJSON().nodes.map(n => [n.id, n]));
                nodes.set(featureId, { ...feature, status: "active" });
                updatedGraph = new FeatureGraph({
                  version: updatedGraph.toJSON().version,
                  nodes,
                  edges: updatedGraph.toJSON().edges,
                  artifacts: updatedGraph.toJSON().artifacts,
                });
              }
            } else {
              invalidFeatureIds.push(featureId);
            }
          }

          if (validFeatureIds.length > 0) {
            await persistFeatureGraph(updatedGraph, state.workspacePath);
            updatedReadyFeatureIds = [...new Set([...updatedReadyFeatureIds, ...validFeatureIds])];

            updatedDesignSession = {
              ...updatedDesignSession,
              phase: "ready_for_development",
              lastActivity: nowIso(),
            };

            updatedChangeHistory.push({
              proposalId: randomUUID(),
              action: "mark_ready",
              timestamp: nowIso(),
              summary: `Marked ready: ${validFeatureIds.join(", ")}`,
            });
          }

          let response = "";
          if (validFeatureIds.length > 0) {
            response += `Marked as ready for development: ${validFeatureIds.join(", ")}.\n`;
          }
          if (invalidFeatureIds.length > 0) {
            response += `Features not found: ${invalidFeatureIds.join(", ")}.`;
          }
          response += `\nRationale: ${args.rationale}`;

          toolMessages.push(recordAction(toolCall.name, toolCallId, response));
          userFacingSummaries.push(response);
          break;
        }

        case "ask_clarifying_question": {
          const args = toolCall.args as z.infer<typeof askClarifyingQuestionSchema>;

          const question: ClarifyingQuestion = {
            id: randomUUID(),
            question: args.question,
            context: args.context,
            relatedFeatureIds: args.relatedFeatureIds,
            options: args.options,
            status: "pending",
            createdAt: nowIso(),
          };

          updatedQuestions.push(question);

          let response = `Question: ${args.question}\nContext: ${args.context}`;
          if (args.options && args.options.length > 0) {
            response += `\nSuggested options:\n${args.options.map((o, i) => `  ${i + 1}. ${o}`).join("\n")}`;
          }

          toolMessages.push(recordAction(toolCall.name, toolCallId, response));
          userFacingSummaries.push(response);
          break;
        }

        case "analyze_impact": {
          const args = toolCall.args as z.infer<typeof analyzeImpactSchema>;

          if (!updatedGraph) {
            const response = "No feature graph available for impact analysis.";
            toolMessages.push(recordAction(toolCall.name, toolCallId, response));
            userFacingSummaries.push(response);
            break;
          }

          const neighbors = updatedGraph.getNeighbors(args.featureId, "both");
          const affectedFeatures = neighbors.map(n => n.id);

          let severity: "none" | "low" | "medium" | "high" = "none";
          if (affectedFeatures.length === 0) {
            severity = "none";
          } else if (affectedFeatures.length <= 2) {
            severity = "low";
          } else if (affectedFeatures.length <= 5) {
            severity = "medium";
          } else {
            severity = "high";
          }

          const analysisKey = `${args.changeType}-${args.featureId}${args.targetFeatureId ? `-${args.targetFeatureId}` : ""}`;
          updatedImpactAnalysis[analysisKey] = {
            affectedFeatures,
            severity,
            description: `${args.changeType} on ${args.featureId} affects ${affectedFeatures.length} features`,
          };

          const response = `Impact Analysis for ${args.changeType} on ${args.featureId}:
- Severity: ${severity.toUpperCase()}
- Affected features: ${affectedFeatures.length > 0 ? affectedFeatures.join(", ") : "None"}
- Description: ${affectedFeatures.length > 0
    ? `This change will affect ${affectedFeatures.join(", ")}. Consider the implications before proceeding.`
    : "This change has no direct impact on other features."}`;

          toolMessages.push(recordAction(toolCall.name, toolCallId, response));
          userFacingSummaries.push(response);
          break;
        }

        case "respond_to_user": {
          const args = toolCall.args as z.infer<typeof respondToUserSchema>;

          let response = args.response;
          if (args.nextSteps) {
            response += `\n\nSuggested next steps: ${args.nextSteps}`;
          }

          toolMessages.push(recordAction(toolCall.name, toolCallId, response));
          userFacingSummaries.push(response);
          break;
        }

        default: {
          toolMessages.push(
            recordAction(
              toolCall.name,
              toolCallId,
              `Unsupported action: ${toolCall.name}`,
            ),
          );
        }
      }
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error ?? "Unknown error");
      logger.error("Design agent action failed", {
        action: toolCall.name,
        error: errorMessage,
      });
      toolMessages.push(
        recordAction(
          toolCall.name,
          toolCallId,
          `Error: ${errorMessage}`,
        ),
      );
      userFacingSummaries.push(`I encountered an error: ${errorMessage}`);
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

  const updates: DesignGraphUpdate = {
    messages: [aiMessage, ...toolMessages, ...(responseMessage ? [responseMessage] : [])],
    featureGraph: updatedGraph,
    pendingProposals: updatedProposals,
    clarifyingQuestions: updatedQuestions,
    readyFeatureIds: updatedReadyFeatureIds,
    changeHistory: updatedChangeHistory,
    impactAnalysis: updatedImpactAnalysis,
    designSession: updatedDesignSession,
  };

  return new Command({
    update: updates,
    goto: END,
  });
}
