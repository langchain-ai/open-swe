import {
  ConfigurableModel,
  initChatModel,
} from "langchain/chat_models/universal";
import { AzureOpenAI } from "openai";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import { createLogger, LogLevel } from "../logger.js";
import {
  LLMTask,
  TASK_TO_CONFIG_DEFAULTS_MAP,
} from "@openswe/shared/open-swe/llm-task";
import { isAllowedUser } from "@openswe/shared/allowed-users";
import { decryptSecret } from "@openswe/shared/crypto";
import { API_KEY_REQUIRED_MESSAGE } from "@openswe/shared/constants";

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
  "openai",
  "anthropic",
  "google-genai",
  "azure-openai",
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
    case "azure-openai":
      return apiKeys.azureApiKey;
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
  async loadModel(graphConfig: GraphConfig, task: LLMTask) {
    const baseConfig = this.getBaseConfigForTask(graphConfig, task);
    const model = await this.initializeModel(baseConfig, graphConfig);
    return model;
  }

  private getUserApiKey(
    graphConfig: GraphConfig,
    provider: Provider,
  ): string | null {
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

    // If the user is allowed, we can return early
    if (isAllowedUser(userLogin)) {
      return null;
    }

    const apiKeys = graphConfig.configurable?.apiKeys as
      | Record<string, string>
      | undefined;
    if (!apiKeys) {
      throw new Error(API_KEY_REQUIRED_MESSAGE);
    }

    const missingProviderKeyMessage = `No API key found for provider: ${provider}. Please add one in the settings page.`;

    const providerApiKey = providerToApiKey(provider, apiKeys);
    if (!providerApiKey) {
      throw new Error(missingProviderKeyMessage);
    }

    const apiKey = decryptSecret(providerApiKey, secretsEncryptionKey);
    if (!apiKey) {
      throw new Error(missingProviderKeyMessage);
    }

    return apiKey;
  }

  /**
   * Initialize the model instance
   */
  public async initializeModel(
    config: ModelLoadConfig,
    graphConfig: GraphConfig,
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

    const apiKey = this.getUserApiKey(graphConfig, provider);

    if (provider === "azure-openai") {
      if (!modelName) {
        throw new Error(
          "Azure OpenAI requires a deployment name. Set AZURE_OPENAI_DEPLOYMENT or configure a model name in GraphConfig.",
        );
      }
      if (/^(gpt|o\d)/i.test(modelName)) {
        throw new Error(
          `Azure OpenAI expects a deployment name, not base model "${modelName}"`,
        );
      }

      const client = new AzureOpenAI({
        apiKey: apiKey ?? process.env.AZURE_OPENAI_API_KEY,
        apiVersion: process.env.AZURE_OPENAI_API_VERSION,
        endpoint: process.env.AZURE_OPENAI_ENDPOINT,
      });

      logger.debug("Initializing model", {
        provider,
        modelName,
      });

      return await initChatModel(modelName, {
        client,
        modelProvider: "azure_openai",
        maxTokens: finalMaxTokens,
        temperature,
      });
    }

    const modelOptions: InitChatModelArgs = {
      modelProvider: provider,
      max_retries: MAX_RETRIES,
      ...(apiKey ? { apiKey } : {}),
      ...(thinkingModel && provider === "anthropic"
        ? {
            thinking: { budget_tokens: thinkingBudgetTokens, type: "enabled" },
            maxTokens: thinkingMaxTokens,
          }
        : modelName.includes("gpt-5")
          ? {
              max_completion_tokens: finalMaxTokens,
              temperature: 1,
            }
          : {
              maxTokens: finalMaxTokens,
              temperature: thinkingModel ? undefined : temperature,
            }),
    };

    logger.debug("Initializing model", {
      provider,
      modelName,
    });

    return await initChatModel(modelName, modelOptions);
  }

  public getModelConfigs(
    config: GraphConfig,
    task: LLMTask,
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
          ...(modelName.includes("gpt-5")
            ? {
                max_completion_tokens:
                  defaultConfig.maxTokens ?? baseConfig.maxTokens,
                temperature: 1,
              }
            : {
                maxTokens: defaultConfig.maxTokens ?? baseConfig.maxTokens,
                temperature:
                  defaultConfig.temperature ?? baseConfig.temperature,
              }),
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
        // Check if fallback model is a thinking model
        const isThinkingModel =
          (provider === "openai" && fallbackModel.modelName.startsWith("o")) ||
          fallbackModel.modelName.includes("extended-thinking");

        const fallbackConfig = {
          ...fallbackModel,
          ...(fallbackModel.modelName.includes("gpt-5")
            ? {
                max_completion_tokens: baseConfig.maxTokens,
                temperature: 1,
              }
            : {
                maxTokens: baseConfig.maxTokens,
                temperature: isThinkingModel
                  ? undefined
                  : baseConfig.temperature,
              }),
          ...(isThinkingModel
            ? {
                thinkingModel: true,
                thinkingBudgetTokens: THINKING_BUDGET_TOKENS,
              }
            : {}),
        };
        configs.push(fallbackConfig);
      }
    }

    return configs;
  }

  /**
   * Get the model name for a task from GraphConfig
   */
  public getModelNameForTask(config: GraphConfig, task: LLMTask): string {
    const baseConfig = this.getBaseConfigForTask(config, task);
    return baseConfig.modelName;
  }

  /**
   * Get base configuration for a task from GraphConfig
   */
  private getBaseConfigForTask(
    config: GraphConfig,
    task: LLMTask,
  ): ModelLoadConfig {
    const taskMap = {
      [LLMTask.PLANNER]: {
        modelName:
          (config.configurable?.[`${task}ModelName`] as string | undefined) ??
          TASK_TO_CONFIG_DEFAULTS_MAP[task].modelName,
        temperature:
          (config.configurable?.[`${task}Temperature`] as number | undefined) ??
          0,
      },
      [LLMTask.PROGRAMMER]: {
        modelName:
          (config.configurable?.[`${task}ModelName`] as string | undefined) ??
          TASK_TO_CONFIG_DEFAULTS_MAP[task].modelName,
        temperature:
          (config.configurable?.[`${task}Temperature`] as number | undefined) ??
          0,
      },
      [LLMTask.REVIEWER]: {
        modelName:
          (config.configurable?.[`${task}ModelName`] as string | undefined) ??
          TASK_TO_CONFIG_DEFAULTS_MAP[task].modelName,
        temperature:
          (config.configurable?.[`${task}Temperature`] as number | undefined) ??
          0,
      },
      [LLMTask.ROUTER]: {
        modelName:
          (config.configurable?.[`${task}ModelName`] as string | undefined) ??
          TASK_TO_CONFIG_DEFAULTS_MAP[task].modelName,
        temperature:
          (config.configurable?.[`${task}Temperature`] as number | undefined) ??
          0,
      },
      [LLMTask.SUMMARIZER]: {
        modelName:
          (config.configurable?.[`${task}ModelName`] as string | undefined) ??
          TASK_TO_CONFIG_DEFAULTS_MAP[task].modelName,
        temperature:
          (config.configurable?.[`${task}Temperature`] as number | undefined) ??
          0,
      },
    } as Record<LLMTask, { modelName: string; temperature: number }>;

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
      ...(modelName.includes("gpt-5")
        ? {
            max_completion_tokens:
              (config.configurable?.maxTokens as number | undefined) ?? 10_000,
            temperature: 1,
          }
        : {
            maxTokens:
              (config.configurable?.maxTokens as number | undefined) ?? 10_000,
            temperature: taskConfig.temperature as number,
          }),
      thinkingModel,
      thinkingBudgetTokens,
    } as ModelLoadConfig;
  }

  /**
   * Get default model for a provider and task
   */
  private getDefaultModelForProvider(
    provider: Provider,
    task: LLMTask,
  ): ModelLoadConfig | null {
    const defaultModels: Record<Provider, Record<LLMTask, string>> = {
      anthropic: {
        [LLMTask.PLANNER]: "claude-sonnet-4-0",
        [LLMTask.PROGRAMMER]: "claude-sonnet-4-0",
        [LLMTask.REVIEWER]: "claude-sonnet-4-0",
        [LLMTask.ROUTER]: "claude-3-5-haiku-latest",
        [LLMTask.SUMMARIZER]: "claude-sonnet-4-0",
      },
      "google-genai": {
        [LLMTask.PLANNER]: "gemini-2.5-flash",
        [LLMTask.PROGRAMMER]: "gemini-2.5-pro",
        [LLMTask.REVIEWER]: "gemini-2.5-flash",
        [LLMTask.ROUTER]: "gemini-2.5-flash",
        [LLMTask.SUMMARIZER]: "gemini-2.5-pro",
      },
      openai: {
        [LLMTask.PLANNER]: "gpt-5",
        [LLMTask.PROGRAMMER]: "gpt-5",
        [LLMTask.REVIEWER]: "gpt-5",
        [LLMTask.ROUTER]: "gpt-5-nano",
        [LLMTask.SUMMARIZER]: "gpt-5-mini",
      },
      "azure-openai": {
        [LLMTask.PLANNER]: process.env.AZURE_OPENAI_DEPLOYMENT ?? "",
        [LLMTask.PROGRAMMER]: process.env.AZURE_OPENAI_DEPLOYMENT ?? "",
        [LLMTask.REVIEWER]: process.env.AZURE_OPENAI_DEPLOYMENT ?? "",
        [LLMTask.ROUTER]: process.env.AZURE_OPENAI_DEPLOYMENT ?? "",
        [LLMTask.SUMMARIZER]: process.env.AZURE_OPENAI_DEPLOYMENT ?? "",
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
