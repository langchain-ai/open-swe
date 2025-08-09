interface Architecture {
  input_modalities: string[];
  output_modalities: string[];
  tokenizer: string;
  instruct_type: string;
}

interface TopProvider {
  is_moderated: boolean;
  context_length: number;
  max_completion_tokens: number;
}

interface Pricing {
  prompt: string;
  completion: string;
  image: string;
  request: string;
  web_search: string;
  internal_reasoning: string;
  input_cache_read: string;
  input_cache_write: string;
}

interface ModelData {
  id: string;
  name: string;
  created: number;
  description: string;
  architecture: Architecture;
  top_provider: TopProvider;
  pricing: Pricing;
  canonical_slug: string;
  context_length: number;
  hugging_face_id: string;
  per_request_limits: Record<string, unknown>;
  supported_parameters: string[];
}

export interface OpenrouterModelsResponse {
  data: ModelData[];
}

export const getOpenrouterModels =
  async (): Promise<OpenrouterModelsResponse> => {
    // List available models (GET /models)
    const response = await fetch(
      "https://openrouter.ai/api/v1/models?category=programming",
      {
        method: "GET",
        headers: {},
      },
    );

    const body = await response.json();
    return body as OpenrouterModelsResponse;
  };
