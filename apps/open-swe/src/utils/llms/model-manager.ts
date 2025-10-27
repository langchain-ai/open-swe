import {
  ConfigurableModel,
  initChatModel,
} from "langchain/chat_models/universal";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import { createLogger, LogLevel } from "../logger.js";
import {
  LLMTask,
  TASK_TO_CONFIG_DEFAULTS_MAP,
} from "@openswe/shared/open-swe/llm-task";
import { isAllowedUser } from "@openswe/shared/github/allowed-users";
import { decryptSecret } from "@openswe/shared/crypto";
import { API_KEY_REQUIRED_MESSAGE } from "@openswe/shared/constants";
import { createNvidiaNimChat } from "../nvidia-nim-chat-wrapper.js";
import { starfleetAuth } from "../starfleet-auth.js";
import { ChatOpenAI } from "@langchain/openai";

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
  "nvidia-gateway", // NVIDIA LLM Gateway → Azure OpenAI (reliable, no tool calling bugs)
  "nvidia-nim", // NVIDIA NIM fallback (cost savings when it works)
  "openai",
  "anthropic",
  "google-genai",
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
    case "nvidia-nim":
      return apiKeys.nvidiaNimApiKey || process.env.NVIDIA_NIM_API_KEY || "";
    case "nvidia-gateway":
      // nvidia-gateway uses Starfleet auth, not a static API key
      // Return empty string - token will be fetched dynamically
      return "";
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

    // NVIDIA NIM: Always use environment variable (bypass user API key requirement)
    if (provider === "nvidia-nim") {
      const nimKey = process.env.NVIDIA_NIM_API_KEY;
      if (!nimKey) {
        logger.warn("NVIDIA_NIM_API_KEY not found in environment, will skip this provider");
      }
      return nimKey || null;
    }

    // NVIDIA Gateway: Uses Starfleet auth (handled separately in initializeModel)
    if (provider === "nvidia-gateway") {
      // Check if LLM Gateway is enabled and credentials are available
      const isEnabled = process.env.NVIDIA_LLM_GATEWAY_ENABLED === "true";
      const hasCredentials = process.env.STARFLEET_ID && process.env.STARFLEET_SECRET;
      
      if (!isEnabled) {
        logger.info("NVIDIA LLM Gateway disabled in environment");
        return null;
      }
      
      if (!hasCredentials) {
        logger.warn("NVIDIA LLM Gateway enabled but Starfleet credentials not configured");
        return null;
      }
      
      // Return a placeholder - actual token will be fetched in initializeModel
      return "starfleet-token-placeholder";
    }

    // If the user is allowed, we can return early
    if (isAllowedUser(userLogin)) {
      return null;
    }

    const apiKeys = graphConfig.configurable?.apiKeys;
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

    // NVIDIA NIM: Use OpenAI-compatible wrapper with custom endpoint
    const isNvidiaNim = provider === "nvidia-nim";
    const isNvidiaGateway = provider === "nvidia-gateway";
    const actualProvider = isNvidiaNim || isNvidiaGateway ? "openai" : provider;
    
    // Get API key - for NVIDIA providers, handle specially
    let apiKey: string | null;
    if (isNvidiaNim) {
      apiKey = process.env.NVIDIA_NIM_API_KEY || null;
      logger.info("Using NVIDIA NIM API key from environment", {
        hasKey: !!apiKey,
        keyPrefix: apiKey ? apiKey.substring(0, 10) + "..." : "none",
      });
    } else if (isNvidiaGateway) {
      // For LLM Gateway, fetch Starfleet token
      try {
        apiKey = await starfleetAuth.getAccessToken();
        logger.info("Using NVIDIA LLM Gateway with Starfleet token", {
          hasToken: !!apiKey,
          tokenInfo: starfleetAuth.getTokenInfo(),
        });
      } catch (error) {
        logger.error("Failed to get Starfleet token for LLM Gateway", {
          error: error instanceof Error ? error.message : String(error),
        });
        throw error;
      }
    } else {
      apiKey = this.getUserApiKey(graphConfig, provider);
    }
    
    const modelOptions: InitChatModelArgs = {
      modelProvider: actualProvider,
      max_retries: MAX_RETRIES,
      ...(apiKey ? { apiKey } : {}),
      // NVIDIA NIM: Override baseURL to point to NVIDIA's endpoint
      ...(isNvidiaNim ? { 
        baseUrl: process.env.NVIDIA_NIM_BASE_URL || "https://integrate.api.nvidia.com/v1" 
      } : {}),
      // NVIDIA Gateway: Override baseURL to point to LLM Gateway
      ...(isNvidiaGateway ? {
        baseUrl: process.env.LLM_GATEWAY_BASE_URL || "https://prod.api.nvidia.com/llm/v1/azure",
      } : {}),
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

    logger.info("Initializing model", {
      originalProvider: provider,
      actualProvider: actualProvider,
      modelName,
      isNvidiaNim,
      isNvidiaGateway,
      baseURL: isNvidiaNim 
        ? (process.env.NVIDIA_NIM_BASE_URL || "https://integrate.api.nvidia.com/v1")
        : isNvidiaGateway
        ? (process.env.LLM_GATEWAY_BASE_URL || "https://prod.api.nvidia.com/llm/v1/azure")
        : "default",
      hasApiKey: !!apiKey,
    });

    // NVIDIA NIM: Use our custom wrapper that handles JSON parsing errors
    if (isNvidiaNim && apiKey) {
      logger.info("Creating NVIDIA NIM ChatOpenAI instance with error handling wrapper", {
        modelName,
        baseURL: process.env.NVIDIA_NIM_BASE_URL || "https://integrate.api.nvidia.com/v1",
      });
      
      return createNvidiaNimChat({
        modelName: modelName,
        apiKey: apiKey,
        baseURL: process.env.NVIDIA_NIM_BASE_URL || "https://integrate.api.nvidia.com/v1",
        maxRetries: MAX_RETRIES,
        maxTokens: finalMaxTokens,
        temperature: thinkingModel ? undefined : temperature,
      });
    }

    // NVIDIA LLM Gateway: Use ChatOpenAI with Azure OpenAI endpoint via NVIDIA gateway
    if (isNvidiaGateway && apiKey) {
      const gatewayModel = process.env.LLM_GATEWAY_MODEL || "gpt-4o-mini";
      const baseURL = process.env.LLM_GATEWAY_BASE_URL || "https://prod.api.nvidia.com/llm/v1/azure";
      const apiVersion = process.env.LLM_GATEWAY_API_VERSION || "2024-12-01-preview";
      
      // Generate correlation ID for tracking (UUID v4 format)
      const correlationId = `${Date.now()}-${Math.random().toString(36).substring(2, 15)}`;
      
      logger.info("Creating NVIDIA LLM Gateway ChatOpenAI instance", {
        model: gatewayModel,
        baseURL,
        apiVersion,
        correlationId,
      });

      const chatModel = new ChatOpenAI({
        modelName: gatewayModel,
        openAIApiKey: apiKey, // This is the Starfleet token
        configuration: {
          baseURL: baseURL,
          defaultQuery: {
            "api-version": apiVersion,
          },
          defaultHeaders: {
            "correlationId": correlationId,
          },
          // Increase timeouts for long-running requests (reviews, complex planning)
          fetch: async (url: string, init?: RequestInit) => {
            return fetch(url, {
              ...init,
              // @ts-expect-error - undici specific timeout options
              bodyTimeout: 300000, // 5 minutes for body
              headersTimeout: 60000, // 1 minute for headers
            });
          },
        },
        maxRetries: 1, // Reduce retries for faster failure to fallback
        maxTokens: finalMaxTokens,
        temperature: thinkingModel ? undefined : temperature,
        streaming: true, // Enable streaming for faster perceived performance
        timeout: 180000, // 3 minute timeout for LLM response (complex tasks need time)
      });

      // Azure OpenAI has a 40-character limit on tool_call IDs
      // LangChain generates longer IDs, so we need to intercept and truncate them
      const originalInvoke = chatModel.invoke.bind(chatModel);
      chatModel.invoke = async function(input: any, options?: any) {
        // Truncate tool_call IDs in messages if they exist
        if (Array.isArray(input)) {
          input = input.map((msg: any) => {
            if (msg.tool_calls && Array.isArray(msg.tool_calls)) {
              msg.tool_calls = msg.tool_calls.map((tc: any) => ({
                ...tc,
                id: tc.id ? tc.id.substring(0, 40) : tc.id,
              }));
            }
            return msg;
          });
        }
        return originalInvoke(input, options);
      };

      return chatModel;
    }

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
          config.configurable?.[`${task}ModelName`] ??
          TASK_TO_CONFIG_DEFAULTS_MAP[task].modelName,
        temperature: config.configurable?.[`${task}Temperature`] ?? 0,
      },
      [LLMTask.PROGRAMMER]: {
        modelName:
          config.configurable?.[`${task}ModelName`] ??
          TASK_TO_CONFIG_DEFAULTS_MAP[task].modelName,
        temperature: config.configurable?.[`${task}Temperature`] ?? 0,
      },
      [LLMTask.REVIEWER]: {
        modelName:
          config.configurable?.[`${task}ModelName`] ??
          TASK_TO_CONFIG_DEFAULTS_MAP[task].modelName,
        temperature: config.configurable?.[`${task}Temperature`] ?? 0,
      },
      [LLMTask.ROUTER]: {
        modelName:
          config.configurable?.[`${task}ModelName`] ??
          TASK_TO_CONFIG_DEFAULTS_MAP[task].modelName,
        temperature: config.configurable?.[`${task}Temperature`] ?? 0,
      },
      [LLMTask.SUMMARIZER]: {
        modelName:
          config.configurable?.[`${task}ModelName`] ??
          TASK_TO_CONFIG_DEFAULTS_MAP[task].modelName,
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
      ...(modelName.includes("gpt-5")
        ? {
            max_completion_tokens: config.configurable?.maxTokens ?? 10_000,
            temperature: 1,
          }
        : {
            maxTokens: config.configurable?.maxTokens ?? 10_000,
            temperature: taskConfig.temperature,
          }),
      thinkingModel,
      thinkingBudgetTokens,
    };
  }

  /**
   * Get default model for a provider and task
   */
  private getDefaultModelForProvider(
    provider: Provider,
    task: LLMTask,
  ): ModelLoadConfig | null {
    const defaultModels: Record<Provider, Record<LLMTask, string>> = {
      "nvidia-nim": {
        // NVIDIA NIM models - Llama 4 Scout (Testing for tool calling)
        [LLMTask.PLANNER]: "meta/llama-4-scout-17b-16e-instruct",
        [LLMTask.PROGRAMMER]: "meta/llama-4-scout-17b-16e-instruct",
        [LLMTask.REVIEWER]: "meta/llama-4-scout-17b-16e-instruct",
        [LLMTask.ROUTER]: "meta/llama-4-scout-17b-16e-instruct",
        [LLMTask.SUMMARIZER]: "meta/llama-4-scout-17b-16e-instruct",
      },
      "nvidia-gateway": {
        // NVIDIA LLM Gateway → Azure OpenAI models (via Starfleet)
        [LLMTask.PLANNER]: "gpt-4o",
        [LLMTask.PROGRAMMER]: "gpt-4o",
        [LLMTask.REVIEWER]: "gpt-4o-mini",
        [LLMTask.ROUTER]: "gpt-4o-mini",
        [LLMTask.SUMMARIZER]: "gpt-4o-mini",
      },
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
