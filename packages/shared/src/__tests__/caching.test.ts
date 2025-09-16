import { tokenDataReducer } from "../caching.js";
import { ModelTokenData } from "../open-swe/types.js";

describe("tokenDataReducer", () => {
  it("should merge objects with the same model string", () => {
    const state: ModelTokenData[] = [
      {
        model: "anthropic:claude-sonnet-4-0",
        cacheCreationInputTokens: 100,
        cacheReadInputTokens: 50,
        inputTokens: 200,
        outputTokens: 150,
        reasoningTokens: 40,
      },
      {
        model: "openai:gpt-4.1-mini",
        cacheCreationInputTokens: 80,
        cacheReadInputTokens: 30,
        inputTokens: 120,
        outputTokens: 90,
        reasoningTokens: 15,
      },
    ];

    const update: ModelTokenData[] = [
      {
        model: "anthropic:claude-sonnet-4-0",
        cacheCreationInputTokens: 25,
        cacheReadInputTokens: 15,
        inputTokens: 75,
        outputTokens: 60,
        reasoningTokens: 10,
      },
      {
        model: "openai:gpt-3.5-turbo",
        cacheCreationInputTokens: 40,
        cacheReadInputTokens: 20,
        inputTokens: 100,
        outputTokens: 80,
        reasoningTokens: 8,
      },
    ];

    const result = tokenDataReducer(state, update);

    // Should have 3 models total (2 from state, 1 merged, 1 new)
    expect(result).toHaveLength(3);

    // Find the merged anthropic model
    const mergedAnthropic = result.find(
      (data) => data.model === "anthropic:claude-sonnet-4-0",
    );
    expect(mergedAnthropic).toEqual({
      model: "anthropic:claude-sonnet-4-0",
      cacheCreationInputTokens: 125, // 100 + 25
      cacheReadInputTokens: 65, // 50 + 15
      inputTokens: 275, // 200 + 75
      outputTokens: 210, // 150 + 60
      reasoningTokens: 50, // 40 + 10
    });

    // Find the unchanged openai gpt-4.1-mini model
    const unchangedOpenAI = result.find(
      (data) => data.model === "openai:gpt-4.1-mini",
    );
    expect(unchangedOpenAI).toEqual({
      model: "openai:gpt-4.1-mini",
      cacheCreationInputTokens: 80,
      cacheReadInputTokens: 30,
      inputTokens: 120,
      outputTokens: 90,
      reasoningTokens: 15,
    });

    // Find the new openai gpt-3.5-turbo model
    const newOpenAI = result.find(
      (data) => data.model === "openai:gpt-3.5-turbo",
    );
    expect(newOpenAI).toEqual({
      model: "openai:gpt-3.5-turbo",
      cacheCreationInputTokens: 40,
      cacheReadInputTokens: 20,
      inputTokens: 100,
      outputTokens: 80,
      reasoningTokens: 8,
    });
  });

  it("should return update array when state is undefined", () => {
    const update: ModelTokenData[] = [
      {
        model: "anthropic:claude-sonnet-4-0",
        cacheCreationInputTokens: 100,
        cacheReadInputTokens: 50,
        inputTokens: 200,
        outputTokens: 150,
        reasoningTokens: 20,
      },
    ];

    const result = tokenDataReducer(undefined, update);

    expect(result).toEqual(update);
  });

  it("should handle empty update array", () => {
    const state: ModelTokenData[] = [
      {
        model: "anthropic:claude-sonnet-4-0",
        cacheCreationInputTokens: 100,
        cacheReadInputTokens: 50,
        inputTokens: 200,
        outputTokens: 150,
        reasoningTokens: 5,
      },
    ];

    const result = tokenDataReducer(state, []);

    expect(result).toEqual(state);
  });

  it("should handle multiple updates for the same model", () => {
    const state: ModelTokenData[] = [
      {
        model: "anthropic:claude-sonnet-4-0",
        cacheCreationInputTokens: 100,
        cacheReadInputTokens: 50,
        inputTokens: 200,
        outputTokens: 150,
        reasoningTokens: 40,
      },
    ];

    const update: ModelTokenData[] = [
      {
        model: "anthropic:claude-sonnet-4-0",
        cacheCreationInputTokens: 25,
        cacheReadInputTokens: 15,
        inputTokens: 75,
        outputTokens: 60,
        reasoningTokens: 12,
      },
      {
        model: "anthropic:claude-sonnet-4-0",
        cacheCreationInputTokens: 10,
        cacheReadInputTokens: 5,
        inputTokens: 30,
        outputTokens: 20,
        reasoningTokens: 3,
      },
    ];

    const result = tokenDataReducer(state, update);

    expect(result).toHaveLength(1);
    expect(result[0]).toEqual({
      model: "anthropic:claude-sonnet-4-0",
      cacheCreationInputTokens: 135, // 100 + 25 + 10
      cacheReadInputTokens: 70, // 50 + 15 + 5
      inputTokens: 305, // 200 + 75 + 30
      outputTokens: 230, // 150 + 60 + 20
      reasoningTokens: 55, // 40 + 12 + 3
    });
  });
});
