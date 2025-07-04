import { initChatModel } from "langchain/chat_models/universal";
import { createLogger, LogLevel } from "./logger.js";

const logger = createLogger(LogLevel.INFO, "LLM_FALLBACK");

// Define fallback models for each provider
const FALLBACK_MODELS = {
  anthropic: "claude-3-5-sonnet-latest",
  openai: "gpt-4o",
  "google-genai": "gemini-2.5-flash-preview-05-20",
};

// Provider order for fallback attempts
const PROVIDER_ORDER = ["anthropic", "openai", "google-genai"] as const;

interface ModelLike {
  invoke: (messages: any[], options?: any) => Promise<any>;
  withConfig: (config: any) => ModelLike;
  bindTools?: (tools: any[]) => ModelLike;
  [key: string]: any;
}

interface FallbackConfig {
  maxTokens?: number;
  temperature?: number;
  originalProvider: string;
  originalModelName: string;
}

export class LLMFallbackWrapper implements ModelLike {
  private models: Map<string, ModelLike> = new Map();
  private config: FallbackConfig;
  private currentConfig: any = {};

  constructor(
    private primaryModel: ModelLike,
    config: FallbackConfig,
  ) {
    this.config = config;
    this.models.set(config.originalProvider, primaryModel);
  }

  private async getFallbackModel(provider: string): Promise<ModelLike> {
    if (this.models.has(provider)) {
      return this.models.get(provider)!;
    }

    const modelName = FALLBACK_MODELS[provider as keyof typeof FALLBACK_MODELS];
    if (!modelName) {
      throw new Error(`No fallback model defined for provider: ${provider}`);
    }

    logger.info(`Creating fallback model for provider: ${provider}`, {
      modelName,
      originalProvider: this.config.originalProvider,
      originalModel: this.config.originalModelName,
    });

    const model = await initChatModel(modelName, {
      modelProvider: provider,
      temperature: this.config.temperature,
      maxTokens: this.config.maxTokens,
    });

    this.models.set(provider, model);
    return model;
  }

  async invoke(messages: any[], options?: any): Promise<any> {
    const providers = [
      this.config.originalProvider,
      ...PROVIDER_ORDER.filter((p) => p !== this.config.originalProvider),
    ];

    for (let i = 0; i < providers.length; i++) {
      const provider = providers[i];

      try {
        const model =
          i === 0 ? this.primaryModel : await this.getFallbackModel(provider);

        const modelWithConfig =
          Object.keys(this.currentConfig).length > 0
            ? model.withConfig(this.currentConfig)
            : model;

        logger.info(`Attempting LLM invoke with provider: ${provider}`, {
          attempt: i + 1,
          totalProviders: providers.length,
          originalProvider: this.config.originalProvider,
        });

        const result = await modelWithConfig.invoke(messages, options);

        if (i > 0) {
          logger.info(`Fallback successful with provider: ${provider}`, {
            failedProvider: this.config.originalProvider,
            successfulProvider: provider,
          });
        }

        return result;
      } catch (error) {
        logger.error(`LLM invoke failed with provider: ${provider}`, {
          error: error instanceof Error ? error.message : String(error),
          attempt: i + 1,
          totalProviders: providers.length,
        });

        if (i === providers.length - 1) {
          logger.error("All LLM providers exhausted", {
            originalProvider: this.config.originalProvider,
            attemptedProviders: providers,
          });
          throw error;
        }
      }
    }

    throw new Error("Unexpected error: no providers attempted");
  }

  withConfig(config: any): LLMFallbackWrapper {
    const wrapper = new LLMFallbackWrapper(this.primaryModel, this.config);
    wrapper.models = this.models;
    wrapper.currentConfig = { ...this.currentConfig, ...config };
    return wrapper;
  }

  bindTools(tools: any[]): LLMFallbackWrapper {
    const wrappedPrimary = this.primaryModel.bindTools
      ? this.primaryModel.bindTools(tools)
      : this.primaryModel;
    const wrapper = new LLMFallbackWrapper(wrappedPrimary, this.config);
    wrapper.models = new Map();
    wrapper.currentConfig = this.currentConfig;

    // Pre-bind tools to cached fallback models
    for (const [provider, model] of this.models.entries()) {
      if (provider !== this.config.originalProvider && model.bindTools) {
        wrapper.models.set(provider, model.bindTools(tools));
      }
    }

    return wrapper;
  }

  // Proxy other methods to the primary model
  [key: string]: any;
}

// Proxy handler to forward unknown method calls to the primary model
export function createLLMFallbackWrapper(
  primaryModel: ModelLike,
  config: FallbackConfig,
): LLMFallbackWrapper {
  const wrapper = new LLMFallbackWrapper(primaryModel, config);

  return new Proxy(wrapper, {
    get(target, prop, receiver) {
      if (prop in target) {
        return Reflect.get(target, prop, receiver);
      }

      // Forward unknown properties to the primary model
      const primaryValue = Reflect.get(target.primaryModel, prop);
      if (typeof primaryValue === "function") {
        return primaryValue.bind(target.primaryModel);
      }
      return primaryValue;
    },
  });
}
