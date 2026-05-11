import { createDeepAgent, LocalShellBackend } from 'deepagents';
import type { DeepAgent } from 'deepagents';
import { createChatModel } from '@agent/model-factory.js';
import { defaultSystemPrompt, evalSystemPrompt } from '@agent/prompts';
import type { ApiKeys, ModelConfig } from '@types';

export const createAgent = async (
  apiKeys: ApiKeys,
  modelConfig: ModelConfig,
  prompt: string = defaultSystemPrompt
): Promise<DeepAgent<any>> => {
  const model = createChatModel(apiKeys, modelConfig, { bindTools: false });
  const backend = await LocalShellBackend.create({
    rootDir: process.cwd(),
    virtualMode: true,
    inheritEnv: true,
    timeout: 120,
  });

  return createDeepAgent({
    model: model as any,
    systemPrompt: prompt,
    backend,
  }) as unknown as DeepAgent<any>;
};

export const createEvalAgent = (
  apiKeys: ApiKeys,
  modelConfig: ModelConfig,
  prompt: string = evalSystemPrompt
) => {
  const model = createChatModel(apiKeys, modelConfig, { bindTools: false });

  return {
    invoke: async (input: { messages: any[] }) => {
      const messages = [
        { role: 'system', content: prompt },
        ...input.messages,
      ];
      const response = await (model as any).invoke(messages);
      return { messages: [...input.messages, response] };
    },
  };
};
