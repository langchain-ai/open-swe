import { Command, END } from "@langchain/langgraph";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import { DesignGraphState } from "@openswe/shared/open-swe/design/types";
import { loadModel, supportsParallelToolCallsParam } from "../../../utils/llms/index.js";
import { LLMTask } from "@openswe/shared/open-swe/llm-task";
import { isHumanMessage } from "@langchain/core/messages";
import { z } from "zod";
import { getMessageContentString } from "@openswe/shared/messages";
import { createLogger, LogLevel } from "../../../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "ClassifyDesignIntent");

const CLASSIFICATION_SYSTEM_PROMPT = `You are a classifier for a feature graph design conversation.
Analyze the user's message and determine their intent.

Possible intents:
1. "design" - User wants to discuss, create, modify, or refine features in the graph
2. "handoff" - User explicitly wants to hand off features to the planner for development
3. "question" - User is asking a question about existing features or the design process
4. "end" - User wants to end the design session

Look for explicit signals:
- "start development", "kick off", "hand off to planner", "ready to develop" → handoff
- "create", "add", "update", "connect", "design", "let's work on" → design
- "what", "how", "why", "explain", "tell me about" → question
- "done", "finished", "that's all", "exit" → end

Default to "design" if the intent is ambiguous and relates to features.`;

const classificationSchema = z.object({
  intent: z.enum(["design", "handoff", "question", "end"]),
  confidence: z.number().min(0).max(1),
  reasoning: z.string(),
});

export async function classifyDesignIntent(
  state: DesignGraphState,
  config: GraphConfig,
): Promise<Command> {
  const userMessage = state.messages.findLast(isHumanMessage);

  if (!userMessage) {
    logger.warn("No user message found, defaulting to design");
    return new Command({ goto: "design-agent" });
  }

  const messageContent = getMessageContentString(userMessage.content).toLowerCase();

  // Quick pattern matching for common explicit intents
  const handoffPatterns = [
    /start\s+development/i,
    /kick\s+off/i,
    /hand\s*off/i,
    /ready\s+to\s+develop/i,
    /begin\s+implementation/i,
    /start\s+building/i,
    /develop\s+(?:this|these|the)/i,
  ];

  const endPatterns = [
    /^done$/i,
    /^exit$/i,
    /^quit$/i,
    /that'?s?\s+all/i,
    /^finished$/i,
    /^end\s+design/i,
  ];

  for (const pattern of handoffPatterns) {
    if (pattern.test(messageContent)) {
      logger.info("Detected handoff intent via pattern match");
      return new Command({ goto: "handoff-to-planner" });
    }
  }

  for (const pattern of endPatterns) {
    if (pattern.test(messageContent)) {
      logger.info("Detected end intent via pattern match");
      return new Command({ goto: END });
    }
  }

  // Use LLM for more nuanced classification
  try {
    const model = await loadModel(config, LLMTask.ROUTER);
    const modelSupportsParallelToolCallsParam = supportsParallelToolCallsParam(
      config,
      LLMTask.ROUTER,
    );

    const classificationTool = {
      name: "classify_intent",
      description: "Classify the user's intent in the design conversation",
      schema: classificationSchema,
    };

    const modelWithTools = model.bindTools([classificationTool], {
      tool_choice: { type: "tool", name: "classify_intent" },
      ...(modelSupportsParallelToolCallsParam
        ? { parallel_tool_calls: false }
        : {}),
    });

    const result = await modelWithTools.invoke([
      { role: "system", content: CLASSIFICATION_SYSTEM_PROMPT },
      {
        role: "user",
        content: `Classify this message: "${getMessageContentString(userMessage.content)}"`,
      },
    ]);

    const toolCall = result.tool_calls?.[0];
    if (toolCall && toolCall.name === "classify_intent") {
      const classification = toolCall.args as z.infer<typeof classificationSchema>;
      logger.info("Classified design intent", {
        intent: classification.intent,
        confidence: classification.confidence,
        reasoning: classification.reasoning,
      });

      switch (classification.intent) {
        case "handoff":
          return new Command({ goto: "handoff-to-planner" });
        case "end":
          return new Command({ goto: END });
        case "question":
        case "design":
        default:
          return new Command({ goto: "design-agent" });
      }
    }
  } catch (error) {
    logger.warn("Classification failed, defaulting to design", {
      error: error instanceof Error ? error.message : String(error),
    });
  }

  // Default to design agent for feature-related conversations
  return new Command({ goto: "design-agent" });
}
