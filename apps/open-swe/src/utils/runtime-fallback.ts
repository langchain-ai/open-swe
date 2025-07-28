import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { Task } from "./llms/index.js";
import { ModelManager, Provider } from "./llms/model-manager.js";
import { createLogger, LogLevel } from "./logger.js";
import { Runnable, RunnableConfig } from "@langchain/core/runnables";
import { StructuredToolInterface } from "@langchain/core/tools";
import {
  ConfigurableChatModelCallOptions,
  ConfigurableModel,
} from "langchain/chat_models/universal";
import { AIMessageChunk, BaseMessage } from "@langchain/core/messages";
import { ChatResult, ChatGeneration } from "@langchain/core/outputs";
import { BaseLanguageModelInput } from "@langchain/core/language_models/base";
import { BindToolsInput } from "@langchain/core/language_models/chat_models";
import { getMessageContentString } from "@open-swe/shared/messages";

const logger = createLogger(LogLevel.DEBUG, "FallbackRunnable");

interface ExtractedTools {
  tools: StructuredToolInterface[];
  kwargs: Record<string, any>;
}

export class FallbackRunnable<
  RunInput extends BaseLanguageModelInput = BaseLanguageModelInput,
  CallOptions extends
    ConfigurableChatModelCallOptions = ConfigurableChatModelCallOptions,
> extends ConfigurableModel<RunInput, CallOptions> {
  private primaryRunnable: any;
  private config: GraphConfig;
  private task: Task;
  private modelManager: ModelManager;
  private providerTools?: Record<Provider, StructuredToolInterface[]>;

  constructor(
    primaryRunnable: any,
    config: GraphConfig,
    task: Task,
    modelManager: ModelManager,
    providerTools?: Record<Provider, StructuredToolInterface[]>,
  ) {
    super({
      configurableFields: "any",
      configPrefix: "fallback",
      queuedMethodOperations: {},
      disableStreaming: false,
    });
    this.primaryRunnable = primaryRunnable;
    this.config = config;
    this.task = task;
    this.modelManager = modelManager;
    this.providerTools = providerTools;
  }

  async _generate(
    messages: BaseMessage[],
    options?: Record<string, any>,
  ): Promise<ChatResult> {
    const result = await this.invoke(messages, options);
    const generation: ChatGeneration = {
      message: result,
      text: result?.content ? getMessageContentString(result.content) : "",
    };
    return {
      generations: [generation],
      llmOutput: {},
    };
  }

  async invoke(
    input: BaseLanguageModelInput,
    options?: Record<string, any>,
  ): Promise<AIMessageChunk> {
    const modelConfigs = this.modelManager.getModelConfigs(
      this.config,
      this.task,
      this.getPrimaryModel(),
    );

    let lastError: Error | undefined;

    for (let i = 0; i < modelConfigs.length; i++) {
      const modelConfig = modelConfigs[i];
      const modelKey = `${modelConfig.provider}:${modelConfig.modelName}`;

      if (!this.modelManager.isCircuitClosed(modelKey)) {
        logger.warn(`Circuit breaker open for ${modelKey}, skipping`);
        continue;
      }

      try {
        const model = await this.modelManager.initializeModel(modelConfig);
        let runnableToUse: Runnable<BaseLanguageModelInput, AIMessageChunk> =
          model;

        // Check if provider-specific tools exist for this provider
        const providerSpecificTools = this.providerTools?.[modelConfig.provider];
        let toolsToUse: ExtractedTools | null = null;
        
        if (providerSpecificTools) {
          // Use provider-specific tools if available
          const extractedTools = this.extractBoundTools();
          toolsToUse = {
            tools: providerSpecificTools,
            kwargs: extractedTools?.kwargs || {},
          };
        } else {
          // Fall back to extracted bound tools from primary model
          toolsToUse = this.extractBoundTools();
        }

        if (toolsToUse && "bindTools" in runnableToUse && runnableToUse.bindTools) {
          runnableToUse = (runnableToUse as ConfigurableModel).bindTools(
            toolsToUse.tools,
            toolsToUse.kwargs,
          );
        }

        const config = this.extractConfig();
        if (config) {
          runnableToUse = runnableToUse.withConfig(config);
        }

        const result = await runnableToUse.invoke(input, options);
        this.modelManager.recordSuccess(modelKey);
        return result;
      } catch (error) {
        logger.warn(
          `${modelKey} failed: ${error instanceof Error ? error.message : String(error)}`,
        );
        lastError = error instanceof Error ? error : new Error(String(error));
        this.modelManager.recordFailure(modelKey);
      }
    }

    throw new Error(
      `All fallback models exhausted for task ${this.task}. Last error: ${lastError?.message}`,
    );
  }

  bindTools(
    tools: BindToolsInput[],
    kwargs?: Record<string, any>,
  ): ConfigurableModel<RunInput, CallOptions> {
    const boundPrimary =
      this.primaryRunnable.bindTools?.(tools, kwargs) ?? this.primaryRunnable;
    return new FallbackRunnable(
      boundPrimary,
      this.config,
      this.task,
      this.modelManager,
    ) as unknown as ConfigurableModel<RunInput, CallOptions>;
  }

  // @ts-expect-error - types are hard man :/
  withConfig(
    config?: RunnableConfig,
  ): ConfigurableModel<RunInput, CallOptions> {
    const configuredPrimary =
      this.primaryRunnable.withConfig?.(config) ?? this.primaryRunnable;
    return new FallbackRunnable(
      configuredPrimary,
      this.config,
      this.task,
      this.modelManager,
    ) as unknown as ConfigurableModel<RunInput, CallOptions>;
  }

  private getPrimaryModel(): ConfigurableModel {
    let current = this.primaryRunnable;

    // Unwrap any LangChain bindings to get to the actual model
    while (current?.bound) {
      current = current.bound;
    }

    // The unwrapped object should be a chat model with _llmType
    if (current && typeof current._llmType !== "undefined") {
      return current;
    }

    throw new Error(
      "Could not extract primary model from runnable - no _llmType found",
    );
  }

  private extractBoundTools(): ExtractedTools | null {
    let current: any = this.primaryRunnable;

    while (current) {
      if (current._queuedMethodOperations?.bindTools) {
        const bindToolsOp = current._queuedMethodOperations.bindTools;

        if (Array.isArray(bindToolsOp) && bindToolsOp.length > 0) {
          const tools = bindToolsOp[0] as StructuredToolInterface[];
          const toolOptions = bindToolsOp[1] || {};

          return {
            tools: tools,
            kwargs: {
              tool_choice: (toolOptions as Record<string, any>).tool_choice,
              parallel_tool_calls: (toolOptions as Record<string, any>)
                .parallel_tool_calls,
            },
          };
        }
      }
      current = current.bound;
    }

    return null;
  }

  private extractConfig(): Partial<RunnableConfig> | null {
    let current: any = this.primaryRunnable;

    while (current) {
      if (current.config) {
        return current.config;
      }
      current = current.bound;
    }

    return null;
  }
}


