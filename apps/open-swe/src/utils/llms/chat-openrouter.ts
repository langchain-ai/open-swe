import {
  BaseChatModel,
  type BaseChatModelParams,
} from "@langchain/core/language_models/chat_models";
import { AIMessage, BaseMessage } from "@langchain/core/messages";
import { ChatResult } from "@langchain/core/outputs";
import { getModelManager } from "./model-manager.js";
import { OpenRouterKeyManager } from "@open-swe/shared/open-swe/openrouter";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { LLMTask } from "@open-swe/shared/open-swe/llm-task";

export interface ChatOpenRouterParams extends BaseChatModelParams {
  modelName: string;
  temperature: number;
  maxTokens: number;
  graphConfig: GraphConfig;
  task: LLMTask;
}

export class ChatOpenRouter extends BaseChatModel {
  private modelName: string;
  private temperature: number;
  private maxTokens: number;
  private keyManager: OpenRouterKeyManager;
  private graphConfig: GraphConfig;
  private task: LLMTask;

  constructor(fields: ChatOpenRouterParams) {
    super(fields);
    this.modelName = fields.modelName;
    this.temperature = fields.temperature;
    this.maxTokens = fields.maxTokens;
    this.graphConfig = fields.graphConfig;
    this.task = fields.task;
    const openRouterKeys =
      (this.graphConfig.configurable?.apiKeys?.openrouter as string[]) ?? [];
    this.keyManager = new OpenRouterKeyManager(openRouterKeys);
  }

  public _llmType(): string {
    return "openrouter";
  }

  public async _generate(
    messages: BaseMessage[],
    options: this["ParsedCallOptions"],
  ): Promise<ChatResult> {
    const modelManager = getModelManager();
    let attempts = 0;
    const maxAttempts = this.keyManager.isAllKeysUsed()
      ? 1
      : this.keyManager.getKeys().length;

    while (attempts < maxAttempts) {
      const apiKey = this.keyManager.getNextKey();
      try {
        const response = await this.invoke(messages, { ...options, apiKey });
        return {
          generations: [
            {
              message: new AIMessage(response.content),
              text:
                typeof response.content === "string"
                  ? response.content
                  : JSON.stringify(response.content),
            },
          ],
          llmOutput: {},
        };
      } catch (error: any) {
        if (error.status === 429 && !this.keyManager.isAllKeysUsed()) {
          this.keyManager.rotateKey();
          attempts++;
        } else if (this.keyManager.isAllKeysUsed()) {
          throw new Error("All OpenRouter API keys have been used.");
        } else {
          throw error;
        }
      }
    }
    throw new Error("Failed to get a response from OpenRouter.");
  }

  public async invoke(
    messages: BaseMessage[],
    options: this["ParsedCallOptions"] & { apiKey: string },
  ): Promise<AIMessage> {
    const response = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${options.apiKey}`,
      },
      body: JSON.stringify({
        model: this.modelName,
        messages: messages.map((m) => ({
          role: m._getType(),
          content: m.content,
        })),
        temperature: this.temperature,
        max_tokens: this.maxTokens,
      }),
    });

    if (!response.ok) {
      const error = new Error(
        `OpenRouter request failed with status ${response.status}`,
      ) as any;
      error.status = response.status;
      throw error;
    }

    const json = await response.json();
    return new AIMessage({
      content: json.choices[0].message.content,
    });
  }

  public _combineLLMOutput(...llmOutputs: any[]): any {
    return {};
  }
}
