import { ChatOllama } from "@langchain/ollama";
import { BaseMessage, AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages";
import { StructuredTool } from "@langchain/core/tools";
import { createLogger, LogLevel } from "../logger.js";

const logger = createLogger(LogLevel.INFO, "OllamaWrapper");

/**
 * Wrapper that makes Ollama models work with SWE agent's tool-based architecture
 * Converts tool calls to natural language prompts and parses responses back
 */
export class OllamaChatWrapper extends ChatOllama {
  private tools: StructuredTool[] = [];
  
  constructor(options: any) {
    super(options);
    logger.info("Initialized OllamaChatWrapper", { model: options.model });
  }

  /**
   * Override the tool binding to store tools for later use
   */
  bindTools(tools: StructuredTool[]) {
    this.tools = tools;
    // Return this instead of calling super.bindTools() which would fail
    return this;
  }

  /**
   * Override invoke to handle tool calling through natural language
   */
  async invoke(
    input: BaseMessage[] | string,
    options?: any
  ): Promise<AIMessage> {
    const messages = Array.isArray(input) ? input : [new HumanMessage(input)];
    
    // Check if the last message is asking for tool usage
    const lastMessage = messages[messages.length - 1];
    
    if (this.shouldUseTool(lastMessage)) {
      return this.handleToolBasedRequest(messages, options);
    }
    
    // Regular text generation - call original Ollama
    return super.invoke(messages, options);
  }

  /**
   * Detect if the request needs tool usage
   */
  private shouldUseTool(message: BaseMessage): boolean {
    const content = message.content.toString().toLowerCase();
    
    // Look for patterns that indicate tool usage
    const toolPatterns = [
      'read file', 'write file', 'list directory', 'run command',
      'search files', 'execute', 'create file', 'modify file',
      'grep', 'find', 'ls', 'cat', 'mkdir', 'rm'
    ];
    
    return toolPatterns.some(pattern => content.includes(pattern));
  }

  /**
   * Handle requests that need tools by converting to natural language
   */
  private async handleToolBasedRequest(
    messages: BaseMessage[],
    options?: any
  ): Promise<AIMessage> {
    const lastMessage = messages[messages.length - 1];
    
    // Create a prompt that includes available tools
    const toolDescriptions = this.tools.map(tool => 
      `- ${tool.name}: ${tool.description}`
    ).join('\n');

    const enhancedPrompt = `You are a helpful coding assistant working in a local development environment.

Available tools:
${toolDescriptions}

IMPORTANT: When you need to use tools, respond in this EXACT format:
TOOL_CALL: <tool_name>
ARGS: <json_args>
REASONING: <why_you_need_this_tool>

For example:
TOOL_CALL: read_file
ARGS: {"path": "src/index.js"}
REASONING: I need to read the file to understand its current structure

User request: ${lastMessage.content}

Please respond with either:
1. A TOOL_CALL if you need to use a tool
2. A direct answer if no tools are needed`;

    // Get response from Ollama
    const response = await super.invoke([new HumanMessage(enhancedPrompt)], options);
    
    // Parse response to see if it's a tool call
    const responseText = response.content.toString();
    
    if (responseText.includes('TOOL_CALL:')) {
      return this.executeToolFromResponse(responseText, messages);
    }
    
    return response;
  }

  /**
   * Execute tool based on Ollama's response
   */
  private async executeToolFromResponse(
    responseText: string,
    originalMessages: BaseMessage[]
  ): Promise<AIMessage> {
    try {
      // Parse the tool call from response
      const toolMatch = responseText.match(/TOOL_CALL:\s*(\w+)/);
      const argsMatch = responseText.match(/ARGS:\s*({.*?})/s);
      const reasoningMatch = responseText.match(/REASONING:\s*(.+?)(?:\n|$)/);
      
      if (!toolMatch || !argsMatch) {
        logger.warn("Could not parse tool call from Ollama response", { responseText });
        return new AIMessage("I apologize, but I couldn't execute that tool call properly.");
      }
      
      const toolName = toolMatch[1];
      const args = JSON.parse(argsMatch[1]);
      const reasoning = reasoningMatch?.[1] || "Tool execution requested";
      
      // Find and execute the tool
      const tool = this.tools.find(t => t.name === toolName);
      if (!tool) {
        logger.warn(`Tool ${toolName} not found`, { availableTools: this.tools.map(t => t.name) });
        return new AIMessage(`Tool '${toolName}' is not available.`);
      }
      
      logger.info("Executing tool via Ollama wrapper", { toolName, args, reasoning });
      
      // Execute the tool
      const toolResult = await tool.invoke(args);
      
      // Return the tool result as if it came from a normal tool call
      return new AIMessage(
        `I executed ${toolName} with reasoning: ${reasoning}\n\nResult: ${toolResult}`
      );
      
    } catch (error) {
      logger.error("Error executing tool from Ollama response", { error, responseText });
      return new AIMessage("I encountered an error while trying to execute that tool.");
    }
  }
}