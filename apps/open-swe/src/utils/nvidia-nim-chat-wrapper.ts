/**
 * NVIDIA NIM ChatOpenAI Wrapper
 * 
 * Wraps ChatOpenAI to intercept responses and fix JSON parsing errors
 * that occur during streaming from NVIDIA NIM endpoints.
 * 
 * The issue: NVIDIA NIM sometimes returns malformed JSON in tool calls,
 * and LangChain's ChatOpenAI throws errors during .invoke() before we
 * can apply our fixes.
 * 
 * Solution: Override the _generate method to catch and fix JSON errors.
 */

import { ChatOpenAI, ChatOpenAICallOptions } from "@langchain/openai";
import { BaseMessage } from "@langchain/core/messages";
import { ChatResult } from "@langchain/core/outputs";
import { createLogger, LogLevel } from "./logger.js";

const logger = createLogger(LogLevel.DEBUG, "NvidiaNimChatWrapper");

export class NvidiaNimChatOpenAI extends ChatOpenAI {
  private baseURL: string;
  private configuredMaxRetries: number;

  constructor(fields: ConstructorParameters<typeof ChatOpenAI>[0]) {
    super(fields);
    this.baseURL = fields?.configuration?.baseURL || "https://integrate.api.nvidia.com/v1";
    this.configuredMaxRetries = fields?.maxRetries || 3;
    
    // CRITICAL: Force non-streaming for NVIDIA NIM to avoid JSON parsing errors
    // The errors occur during streaming response chunk parsing, not after completion
    logger.warn("NvidiaNimChatOpenAI: Forcing streaming=false to prevent JSON parsing errors during streaming");
    (this as any).streaming = false;
  }

  /**
   * Override _generate to catch JSON parsing errors and apply fixes
   */
  async _generate(
    messages: BaseMessage[],
    options?: Partial<ChatOpenAICallOptions>,
    runManager?: any,
  ): Promise<ChatResult> {
    try {
      logger.debug("NvidiaNimChatOpenAI _generate called", {
        messageCount: messages.length,
        hasOptions: !!options,
      });

      // Call parent implementation
      const result = await super._generate(messages, options as any, runManager);
      
      logger.debug("NvidiaNimChatOpenAI _generate succeeded", {
        generationCount: result.generations?.length,
      });

      return result;
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      
      logger.debug("NvidiaNimChatOpenAI _generate caught error", {
        error: errorMessage,
        isJsonError: errorMessage.includes("Expecting"),
      });

      // Check if it's a JSON parsing error
      if (errorMessage.includes("Expecting") || errorMessage.includes("JSON")) {
        logger.warn("JSON parsing error detected in NVIDIA NIM response, attempting recovery", {
          error: errorMessage,
        });

        // Try to make a raw API call and manually parse the response
        try {
          const recovered = await this.attemptRecovery(messages, options);
          if (recovered) {
            logger.info("Successfully recovered from JSON parsing error");
            return recovered;
          }
        } catch (recoveryError) {
          logger.error("Recovery attempt failed", {
            recoveryError: recoveryError instanceof Error ? recoveryError.message : String(recoveryError),
          });
        }
      }

      // If we can't recover, rethrow the original error
      throw error;
    }
  }

  /**
   * Attempt to recover from JSON parsing errors by making a non-streaming call
   */
  private async attemptRecovery(
    messages: BaseMessage[],
    options?: Partial<ChatOpenAICallOptions>,
  ): Promise<ChatResult | null> {
    logger.debug("Attempting recovery with non-streaming call");

    try {
      // Create a new instance with streaming disabled
      const nonStreamingClient = new ChatOpenAI({
        modelName: this.modelName,
        openAIApiKey: this.openAIApiKey,
        configuration: {
          baseURL: this.baseURL,
        },
        maxRetries: this.configuredMaxRetries,
        maxTokens: this.maxTokens,
        temperature: this.temperature,
        streaming: false, // Force non-streaming
      });

      // Try the call again (cast to any to avoid type issues)
      const result = await nonStreamingClient._generate(messages, options as any);
      
      logger.debug("Recovery call succeeded", {
        generationCount: result.generations?.length,
      });

      return result;
    } catch (error) {
      logger.error("Recovery call also failed", {
        error: error instanceof Error ? error.message : String(error),
      });
      return null;
    }
  }
}

/**
 * Create a NVIDIA NIM-compatible ChatOpenAI instance
 */
export function createNvidiaNimChat(options: {
  modelName: string;
  apiKey: string;
  baseURL?: string;
  maxRetries?: number;
  maxTokens?: number;
  temperature?: number;
}): ChatOpenAI {
  logger.debug("Creating NvidiaNimChatOpenAI instance", {
    modelName: options.modelName,
    baseURL: options.baseURL || "https://integrate.api.nvidia.com/v1",
  });

  return new NvidiaNimChatOpenAI({
    modelName: options.modelName,
    openAIApiKey: options.apiKey,
    configuration: {
      baseURL: options.baseURL || "https://integrate.api.nvidia.com/v1",
    },
    maxRetries: options.maxRetries || 3,
    maxTokens: options.maxTokens || 10000,
    temperature: options.temperature,
    streaming: false, // CRITICAL: Disable streaming in constructor fields
  });
}

