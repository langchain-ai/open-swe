import {
  ConfigurableModel,
  initChatModel,
} from "langchain/chat_models/universal";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { createLogger, LogLevel } from "./logger.js";
import { Task } from "./load-model.js";
import { isAllowedUser } from "@open-swe/shared/github/allowed-users";
import { decryptSecret } from "@open-swe/shared/crypto";

const logger = createLogger(LogLevel.INFO, "ModelManager");

type InitChatModelArgs = Parameters<typeof initChatModel>[1];

export interface CircuitBreakerState {
  state: CircuitState;
  failureCount: number;
  lastFailureTime: number;
  openedAt?: number;
}

interface ModelLoadConfig {
  provider: Provider;
  modelName: string;
  temperature?: number;
  maxTokens?: number;
  thinkingModel?: boolean;
  thinkingBudgetTokens?: number;
}

export enum CircuitState {
  /*
   * CLOSED: Normal operation
   */
  CLOSED = "CLOSED",
  /*
   * OPEN: Failing, use fallback
   */
  OPEN = "OPEN",
}

export const PROVIDER_FALLBACK_ORDER = [
  "google-genai",
  "anthropic",
  "openai",
] as const;
export type Provider = (typeof PROVIDER_FALLBACK_ORDER)[number];

export interface ModelManagerConfig {
  /*
   * Failures before opening circuit
   */
  circuitBreakerFailureThreshold: number;
  /*
   * Time to wait before trying again (ms)
   */
  circuitBreakerTimeoutMs: number;
  fallbackOrder: Provider[];
}

export const DEFAULT_MODEL_MANAGER_CONFIG: ModelManagerConfig = {
  circuitBreakerFailureThreshold: 2, // TBD, need to test
  circuitBreakerTimeoutMs: 180000, // 3 minutes timeout
  fallbackOrder: [...PROVIDER_FALLBACK_ORDER],
};

const MAX_RETRIES = 3;
const THINKING_BUDGET_TOKENS = 5000;

const providerToApiKey = (
  providerName: string,
  apiKeys: Record<string, string>,
): string => {
  switch (providerName) {
    case "openai":
      return apiKeys.openaiApiKey;
    case "anthropic":
      return apiKeys.anthropicApiKey;
    case "google-genai":
      return apiKeys.googleApiKey;
    default:
      throw new Error(`Unknown provider: ${providerName}`);
  }
};

export class ModelManager {
  private config: ModelManagerConfig;
  private circuitBreakers: Map<string, CircuitBreakerState> = new Map();

  constructor(config: Partial<ModelManagerConfig> = {}) {
    this.config = { ...DEFAULT_MODEL_MANAGER_CONFIG, ...config };

    logger.info("Initialized", {
      config: this.config,
      fallbackOrder: this.config.fallbackOrder,
    });
  }

  /**
   * Load a single model (no fallback during loading)
   */
  async loadModel(graphConfig: GraphConfig, task: Task) {
    const baseConfig = this.getBaseConfigForTask(graphConfig, task);
    const model = await this.initializeModel(baseConfig, graphConfig);
    return model;
  }
  /**
   * Initialize the model instance
   */
  public async initializeModel(
    config: ModelLoadConfig,
    graphConfig?: GraphConfig,
  ) {
    const {
      provider,
      modelName,
      temperature,
      maxTokens,
      thinkingModel,
      thinkingBudgetTokens,
    } = config;

    const thinkingMaxTokens = thinkingBudgetTokens
      ? thinkingBudgetTokens * 4
      : undefined;

    let finalMaxTokens = maxTokens ?? 10_000;
    if (modelName.includes("claude-3-5-haiku")) {
      finalMaxTokens = finalMaxTokens > 8_192 ? 8_192 : finalMaxTokens;
    }

    let apiKey: string | null = null;
    if (graphConfig) {
      const userLogin = (graphConfig.configurable as any)?.langgraph_auth_user
        ?.display_name;
      const secretsEncryptionKey = process.env.SECRETS_ENCRYPTION_KEY;
      if (!secretsEncryptionKey) {
        throw new Error(
          "SECRETS_ENCRYPTION_KEY environment variable is required",
        );
      }
      if (!userLogin) {
        throw new Error("User login not found in config");
      }
      const apiKeys = graphConfig.configurable?.apiKeys;
      if (!isAllowedUser(userLogin)) {
        if (!apiKeys) {
          throw new Error("API keys not found in config");
        }
        apiKey = decryptSecret(
          providerToApiKey(provider, apiKeys),
          secretsEncryptionKey,
        );
        if (!apiKey) {
          throw new Error("No API key found for provider: " + provider);
        }
      }
    }

    const modelOptions: InitChatModelArgs = {
      modelProvider: provider,
      temperature: thinkingModel ? undefined : temperature,
      max_retries: MAX_RETRIES,
      ...(apiKey ? { apiKey } : {}),
      ...(thinkingModel && provider === "anthropic"
        ? {
            thinking: { budget_tokens: thinkingBudgetTokens, type: "enabled" },
            maxTokens: thinkingMaxTokens,
          }
        : { maxTokens: finalMaxTokens }),
    };

    logger.debug("Initializing model", {
      provider,
      modelName,
    });

    return await initChatModel(modelName, modelOptions);
  }

  public getModelConfigs(
    config: GraphConfig,
    task: Task,
    selectedModel: ConfigurableModel,
  ) {
    const configs: ModelLoadConfig[] = [];
    const baseConfig = this.getBaseConfigForTask(config, task);

    const defaultConfig = selectedModel._defaultConfig;
    let selectedModelConfig: ModelLoadConfig | null = null;

    if (defaultConfig) {
      const provider = defaultConfig.modelProvider as Provider;
      const modelName = defaultConfig.model;

      if (provider && modelName) {
        const isThinkingModel = baseConfig.thinkingModel;
        selectedModelConfig = {
          provider,
          modelName,
          temperature: defaultConfig.temperature ?? baseConfig.temperature,
          maxTokens: defaultConfig.maxTokens ?? baseConfig.maxTokens,
          ...(isThinkingModel
            ? {
                thinkingModel: true,
                thinkingBudgetTokens: THINKING_BUDGET_TOKENS,
              }
            : {}),
        };
        configs.push(selectedModelConfig);
      }
    }

    // Add fallback models
    for (const provider of this.config.fallbackOrder) {
      const fallbackModel = this.getDefaultModelForProvider(provider, task);
      if (
        fallbackModel &&
        (!selectedModelConfig ||
          fallbackModel.modelName !== selectedModelConfig.modelName)
      ) {
        const fallbackConfig = {
          ...fallbackModel,
          temperature: baseConfig.temperature,
          maxTokens: baseConfig.maxTokens,
        };
        configs.push(fallbackConfig);
      }
    }

    return configs;
  }

  /**
   * Get base configuration for a task from GraphConfig
   */
  private getBaseConfigForTask(
    config: GraphConfig,
    task: Task,
  ): ModelLoadConfig {
    const taskMap = {
      [Task.PROGRAMMER]: {
        modelName:
          config.configurable?.[`${task}ModelName`] ??
          "anthropic:claude-sonnet-4-0",
        temperature: config.configurable?.[`${task}Temperature`] ?? 0,
      },
      [Task.ROUTER]: {
        modelName:
          config.configurable?.[`${task}ModelName`] ??
          "anthropic:claude-3-5-haiku-latest",
        temperature: config.configurable?.[`${task}Temperature`] ?? 0,
      },
      [Task.SUMMARIZER]: {
        modelName:
          config.configurable?.[`${task}ModelName`] ??
          "anthropic:claude-sonnet-4-0",
        temperature: config.configurable?.[`${task}Temperature`] ?? 0,
      },
    };

    const taskConfig = taskMap[task];
    const modelStr = taskConfig.modelName;
    const [modelProvider, ...modelNameParts] = modelStr.split(":");

    let thinkingModel = false;
    if (modelNameParts[0] === "extended-thinking") {
      thinkingModel = true;
      modelNameParts.shift();
    }

    const modelName = modelNameParts.join(":");
    if (modelProvider === "openai" && modelName.startsWith("o")) {
      thinkingModel = true;
    }

    const thinkingBudgetTokens = THINKING_BUDGET_TOKENS;

    return {
      modelName,
      provider: modelProvider as Provider,
      temperature: taskConfig.temperature,
      maxTokens: config.configurable?.maxTokens ?? 10_000,
      thinkingModel,
      thinkingBudgetTokens,
    };
  }

  /**
   * Get default model for a provider and task
   */
  private getDefaultModelForProvider(
    provider: Provider,
    task: Task,
  ): ModelLoadConfig | null {
    const defaultModels: Record<Provider, Record<Task, string>> = {
      anthropic: {
        [Task.PROGRAMMER]: "claude-sonnet-4-0",
        [Task.ROUTER]: "claude-3-5-haiku-latest",
        [Task.SUMMARIZER]: "claude-sonnet-4-0",
      },
      "google-genai": {
        [Task.PROGRAMMER]: "gemini-2.5-pro",
        [Task.ROUTER]: "gemini-2.5-flash",
        [Task.SUMMARIZER]: "gemini-2.5-pro",
      },
      openai: {
        [Task.PROGRAMMER]: "gpt-4o",
        [Task.ROUTER]: "gpt-4o-mini",
        [Task.SUMMARIZER]: "gpt-4o",
      },
    };

    const modelName = defaultModels[provider][task];
    if (!modelName) {
      return null;
    }
    return { provider, modelName };
  }

  /**
   * Circuit breaker methods
   */
  public isCircuitClosed(modelKey: string): boolean {
    const state = this.getCircuitState(modelKey);

    if (state.state === CircuitState.CLOSED) {
      return true;
    }

    if (state.state === CircuitState.OPEN && state.openedAt) {
      const timeElapsed = Date.now() - state.openedAt;
      if (timeElapsed >= this.config.circuitBreakerTimeoutMs) {
        state.state = CircuitState.CLOSED;
        state.failureCount = 0;
        delete state.openedAt;

        logger.info(
          `${modelKey}: Circuit breaker automatically recovered: OPEN → CLOSED`,
          {
            timeElapsed: (timeElapsed / 1000).toFixed(1) + "s",
          },
        );
        return true;
      }
    }

    return false;
  }

  private getCircuitState(modelKey: string): CircuitBreakerState {
    if (!this.circuitBreakers.has(modelKey)) {
      this.circuitBreakers.set(modelKey, {
        state: CircuitState.CLOSED,
        failureCount: 0,
        lastFailureTime: 0,
      });
    }
    return this.circuitBreakers.get(modelKey)!;
  }

  public recordSuccess(modelKey: string): void {
    const circuitState = this.getCircuitState(modelKey);

    circuitState.state = CircuitState.CLOSED;
    circuitState.failureCount = 0;
    delete circuitState.openedAt;

    logger.debug(`${modelKey}: Circuit breaker reset after successful request`);
  }

  public recordFailure(modelKey: string): void {
    const circuitState = this.getCircuitState(modelKey);
    const now = Date.now();

    circuitState.lastFailureTime = now;
    circuitState.failureCount++;

    if (
      circuitState.failureCount >= this.config.circuitBreakerFailureThreshold
    ) {
      circuitState.state = CircuitState.OPEN;
      circuitState.openedAt = now;

      logger.warn(
        `${modelKey}: Circuit breaker opened after ${circuitState.failureCount} failures`,
        {
          timeoutMs: this.config.circuitBreakerTimeoutMs,
          willRetryAt: new Date(
            now + this.config.circuitBreakerTimeoutMs,
          ).toISOString(),
        },
      );
    }
  }

  /**
   * Monitoring and observability methods
   */
  public getCircuitBreakerStatus(): Map<string, CircuitBreakerState> {
    return new Map(this.circuitBreakers);
  }

  /**
   * Cleanup on shutdown
   */
  public shutdown(): void {
    this.circuitBreakers.clear();
    logger.info("Shutdown complete");
  }
}

let globalModelManager: ModelManager | null = null;

export function getModelManager(
  config?: Partial<ModelManagerConfig>,
): ModelManager {
  if (!globalModelManager) {
    globalModelManager = new ModelManager(config);
  }
  return globalModelManager;
}

export function resetModelManager(): void {
  if (globalModelManager) {
    globalModelManager.shutdown();
    globalModelManager = null;
  }
}

