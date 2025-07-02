import { tool } from "@langchain/core/tools";
import { createLangGraphDocsReadFields } from "@open-swe/shared/open-swe/tools";
import { getMessageContentString } from "@open-swe/shared/messages";
import { createLogger, LogLevel } from "../utils/logger.js";
import { LANGGRAPH_DOCUMENTATION } from "@open-swe/shared/constants";
import { loadModel, Task } from "../utils/load-model.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";


const LANGGRAPH_DOCS_SYSTEM_PROMPT = `You are a LangGraph documentation assistant. Your role is to provide accurate, specific answers about LangGraph APIs, syntax, patterns, and implementation details based solely on the provided documentation.

## LangGraph Documentation:
{LANGGRAPH_DOCUMENTATION}

## Instructions:
1. **Ground your responses strictly in the provided documentation** - only use information that is explicitly mentioned in the documentation above
2. **Provide specific, actionable answers** with correct syntax and method signatures
3. **Include code examples** when relevant, using the exact patterns shown in the documentation
4. **Reference specific imports** and class names as they appear in the documentation
5. **If the documentation doesn't contain enough information** to answer the query, clearly state what information is missing rather than making assumptions
6. **Focus on practical implementation** - provide code snippets that can be directly used
7. **Maintain accuracy** - double-check that all method names, parameters, and syntax match the documentation exactly

## Response Format:
- Start with a direct answer to the question
- Provide relevant code examples with proper imports
- Include any important notes or gotchas mentioned in the documentation
- Keep responses concise but complete

Your goal is to help developers implement LangGraph correctly by providing documentation-grounded, immediately usable information.`;

const LANGGRAPH_DOCS_USER_PROMPT = `Based on the LangGraph documentation provided above, please answer this question:

{USER_QUERY}

Provide accurate syntax, specific method calls, and practical examples that can be implemented directly.`;


const logger = createLogger(LogLevel.INFO, "LangGraphDocsReadTool");

export function createLangGraphDocsReadTool(
  config: GraphConfig
) {
  const langGraphDocsReadTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      try {
        logger.info("Querying LangGraph documentation", { query: input.query });

        const model = await loadModel(config, Task.SUMMARIZER)

        const formattedSystemPrompt = LANGGRAPH_DOCS_SYSTEM_PROMPT.replaceAll(
            "{LANGGRAPH_DOCUMENTATION}",
            LANGGRAPH_DOCUMENTATION
          );
          
          const formattedUserPrompt = LANGGRAPH_DOCS_USER_PROMPT.replaceAll(
            "{USER_QUERY}",
            input.query
          );
        const response = await model.invoke([
            {
                role: "system",
                content: formattedSystemPrompt
            },
            {
                role: "user",
                content: formattedUserPrompt
            }
        ])

        if (response.content.length === 0) {
            throw new Error("FAILED TO QUERY LANGGRAPH DOCS: No response from model");
        }

        logger.info("LangGraph docs query completed", { 
          query: input.query, 
          responseLength: response.content.length 
        });

        return {
          result: getMessageContentString(response.content),
          status: "success",
        };
      } catch (e) {
        logger.error(
          "Failed to query LangGraph docs: " +
            (e instanceof Error ? e.message : "Unknown error"),
          { error: e, query: input.query }
        );
        
        throw new Error(
          "FAILED TO QUERY LANGGRAPH DOCS: " +
            (e instanceof Error ? e.message : "Unknown error")
        );
      }
    },
    createLangGraphDocsReadFields(),
  );

  return langGraphDocsReadTool;
}