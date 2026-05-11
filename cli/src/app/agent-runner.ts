import { BaseMessage, HumanMessage } from '@langchain/core/messages';
import { createAgent } from '@agent/graph';
import { defaultSystemPrompt, reviewSystemPrompt } from '@agent/prompts';
import { processStreamUpdate } from '@app/stream-processor.js';
import { saveSession } from '@lib/storage';
import { logError } from '@lib/logger';
import { AGENT_RECURSION_LIMIT } from '@lib/constants';
import type { RunnerDeps, StreamProcessorActions } from '@types';

export async function runAgentStream(
  deps: RunnerDeps,
  conversationHistory: { current: BaseMessage[] },
  finalPrompt: string | BaseMessage,
  systemPrompt: string = defaultSystemPrompt
) {
  const { apiKeys, modelConfig, addMessage, updateToolExecution, updateTokenUsage, setBusy } = deps;
  const agentInstance = await createAgent(apiKeys, modelConfig, systemPrompt);
  const userMessage =
    typeof finalPrompt === 'string' ? new HumanMessage(finalPrompt) : finalPrompt;
  conversationHistory.current.push(userMessage);
  await saveSession('last_session', conversationHistory.current);
  setBusy(true);
  try {
    const actions: StreamProcessorActions = { addMessage, updateToolExecution, updateTokenUsage };
    const stream = await agentInstance.stream(
      { messages: conversationHistory.current } as any,
      { recursionLimit: AGENT_RECURSION_LIMIT, streamMode: 'updates' as any }
    );
    for await (const chunk of stream) {
      await processStreamUpdate(chunk as Record<string, any>, conversationHistory, actions);
    }
  } catch (error) {
    const errorMsg = `An error occurred: ${error instanceof Error ? error.message : String(error)}`;
    await logError(errorMsg);
    addMessage({ author: 'system', chunks: [{ kind: 'error', text: errorMsg }] });
  } finally {
    setBusy(false);
  }
}

export async function runReview(
  deps: RunnerDeps,
  conversationHistory: { current: BaseMessage[] },
  systemPrompt: string = reviewSystemPrompt
) {
  const { apiKeys, modelConfig, addMessage, updateToolExecution, updateTokenUsage, setBusy } = deps;
  const reviewAgent = await createAgent(apiKeys, modelConfig, systemPrompt);
  const userMessage = new HumanMessage(
    'Please conduct a code review of the current branch against the base branch (main or master).'
  );
  conversationHistory.current.push(userMessage);
  addMessage({ author: 'system', chunks: [{ kind: 'text', text: 'Starting code review...' }] });
  await saveSession('last_session', conversationHistory.current);
  setBusy(true);
  try {
    const actions: StreamProcessorActions = { addMessage, updateToolExecution, updateTokenUsage };
    const stream = await reviewAgent.stream(
      { messages: conversationHistory.current } as any,
      { recursionLimit: AGENT_RECURSION_LIMIT, streamMode: 'updates' as any }
    );
    for await (const chunk of stream) {
      await processStreamUpdate(chunk as Record<string, any>, conversationHistory, actions);
    }
  } catch (error) {
    const errorMsg = `An error occurred: ${error instanceof Error ? error.message : String(error)}`;
    await logError(errorMsg);
    addMessage({ author: 'system', chunks: [{ kind: 'error', text: errorMsg }] });
  } finally {
    setBusy(false);
  }
}
