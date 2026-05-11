import { ChatOpenAI } from '@langchain/openai';
import { ChatAnthropic } from '@langchain/anthropic';
import { ChatGoogleGenerativeAI } from '@langchain/google-genai';
import type { ModelConfig, ApiKeys } from '@types';

type ChatModelOptions = {
  bindTools?: boolean;
};

export function createChatModel(
  apiKeys: ApiKeys,
  modelConfig: ModelConfig,
  _options: ChatModelOptions = { bindTools: false }
) {
  const { provider, name, effort } = modelConfig;

  switch (provider) {
    case 'openai':
      return new ChatOpenAI({
        apiKey: apiKeys.openai,
        model: name,
        temperature: 1,
        // gpt-5.x rejects `reasoning_effort` + function tools on
        // /v1/chat/completions; the Responses API supports both, but it
        // nests these under `reasoning.effort` and `text.verbosity`.
        useResponsesApi: true,
        modelKwargs: {
          reasoning: { effort },
          text: { verbosity: 'medium' },
        },
      });
    case 'anthropic':
      return new ChatAnthropic({
        apiKey: apiKeys.anthropic,
        model: name,
        temperature: 1,
        maxTokens: 8192,
        invocationKwargs: {
          thinking: { type: 'adaptive' },
          output_config: { effort },
        },
      });
    case 'google':
      return new ChatGoogleGenerativeAI({
        apiKey: apiKeys.google,
        model: name,
        temperature: 1,
      });
    default:
      throw new Error(`Unknown provider: ${provider}`);
  }
}
