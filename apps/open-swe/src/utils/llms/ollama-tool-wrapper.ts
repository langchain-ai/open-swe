import { ToolCall } from "@langchain/core/messages";
import { logger } from "../logger.js";

/**
 * Converts tool calls to text prompts for Ollama models
 * Since Ollama doesn't support native tool calling, we simulate it with prompts
 */
export class OllamaToolWrapper {
  
  /**
   * Convert a tool call to a text prompt that Ollama can understand
   */
  static convertToolCallToPrompt(toolCall: ToolCall): string {
    const { name, args } = toolCall;
    
    switch (name) {
      case "read_file":
        return `Please read the file at path "${args.path}" and return its contents. If the file doesn't exist, return "File not found".`;
        
      case "write_file":
        return `Please write the following content to file "${args.path}":\n\n${args.content}\n\nReturn "File written successfully" when done.`;
        
      case "list_directory":
        return `Please list all files and directories in "${args.path}". Return them as a simple list.`;
        
      case "run_command":
        return `Please execute the shell command: ${args.command}\nReturn the output of the command.`;
        
      case "search_files":
        return `Please search for the pattern "${args.pattern}" in files. Return matching files and line numbers.`;
        
      default:
        logger.warn(`Unknown tool call: ${name}`, { args });
        return `Please help with the following request: ${name} with parameters ${JSON.stringify(args)}`;
    }
  }
  
  /**
   * Check if a model supports native tool calling
   */
  static supportsToolCalling(modelName: string): boolean {
    // List of Ollama models that support some form of tool calling
    const toolCapableModels = [
      "mixtral",
      "codellama", 
      "deepseek-coder",
      // Add more as they become available
    ];
    
    return toolCapableModels.some(model => modelName.includes(model));
  }
  
  /**
   * Parse Ollama response to extract tool results
   * This is a simple implementation - in practice you'd need more sophisticated parsing
   */
  static parseToolResponse(response: string, originalToolCall: ToolCall): any {
    // For now, return the raw response
    // In a full implementation, you'd parse structured responses
    return {
      tool_call_id: originalToolCall.id,
      content: response.trim()
    };
  }
}