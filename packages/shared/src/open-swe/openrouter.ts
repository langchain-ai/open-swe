import { z } from "zod";

export const OpenRouterProvider = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  pricing: z.object({
    prompt: z.string(),
    completion: z.string(),
    request: z.string(),
    image: z.string(),
  }),
  context_length: z.number(),
  architecture: z.object({
    modality: z.string(),
    tokenizer: z.string(),
    instruct_type: z.string().nullable(),
  }),
  top_provider: z.object({
    max_completion_tokens: z.number().nullable(),
    is_moderated: z.boolean(),
  }),
  per_request_limits: z
    .object({
      prompt_tokens: z.number(),
      completion_tokens: z.number(),
    })
    .nullable(),
});

export type OpenRouterProvider = z.infer<typeof OpenRouterProvider>;

export async function getOpenRouterModels(
  apiKey: string,
): Promise<OpenRouterProvider[]> {
  try {
    const response = await fetch("https://openrouter.ai/api/v1/models", {
      headers: {
        Authorization: `Bearer ${apiKey}`,
      },
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch models: ${response.statusText}`);
    }
    const json = (await response.json()) as { data: OpenRouterProvider[] };
    return json.data;
  } catch (error) {
    console.error("Error fetching OpenRouter models:", error);
    return [];
  }
}

export class OpenRouterKeyManager {
  private keys: string[];
  private currentIndex: number;

  constructor(keys: string[]) {
    this.keys = keys;
    this.currentIndex = 0;
  }

  public getNextKey(): string {
    if (this.keys.length === 0) {
      throw new Error("No OpenRouter API keys provided.");
    }

    const key = this.keys[this.currentIndex];
    return key;
  }

  public rotateKey(): void {
    this.currentIndex = (this.currentIndex + 1) % this.keys.length;
  }

  public isAllKeysUsed(): boolean {
    return this.currentIndex === this.keys.length -1;
  }

  public getKeys(): string[] {
    return this.keys;
  }
}
