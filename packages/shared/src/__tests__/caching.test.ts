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
      },
      {
        model: "openai:gpt-4.1-mini",
        cacheCreationInputTokens: 80,
        cacheReadInputTokens: 30,
        inputTokens: 120,
        outputTokens: 90,
      },
    ];

    const update: ModelTokenData[] = [
      {
        model: "anthropic:claude-sonnet-4-0",
        cacheCreationInputTokens: 25,
        cacheReadInputTokens: 15,
        inputTokens: 75,
        outputTokens: 60,
      },
      {
        model: "openai:gpt-3.5-turbo",
        cacheCreationInputTokens: 40,
        cacheReadInputTokens: 20,
        inputTokens: 100,
        outputTokens: 80,
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
      },
    ];

    const update: ModelTokenData[] = [
      {
        model: "anthropic:claude-sonnet-4-0",
        cacheCreationInputTokens: 25,
        cacheReadInputTokens: 15,
        inputTokens: 75,
        outputTokens: 60,
      },
      {
        model: "anthropic:claude-sonnet-4-0",
        cacheCreationInputTokens: 10,
        cacheReadInputTokens: 5,
        inputTokens: 30,
        outputTokens: 20,
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
    });
  });

  describe("replaceMode functionality", () => {
    it("should replace state entirely when replaceMode is true", () => {
      const state: ModelTokenData[] = [
        {
          model: "anthropic:claude-sonnet-4-0",
          cacheCreationInputTokens: 100,
          cacheReadInputTokens: 50,
          inputTokens: 200,
          outputTokens: 150,
        },
        {
          model: "openai:gpt-4.1-mini",
          cacheCreationInputTokens: 80,
          cacheReadInputTokens: 30,
          inputTokens: 120,
          outputTokens: 90,
        },
      ];

      const update: ModelTokenData[] = [
        {
          model: "openai:gpt-3.5-turbo",
          cacheCreationInputTokens: 40,
          cacheReadInputTokens: 20,
          inputTokens: 100,
          outputTokens: 80,
        },
      ];

      const result = tokenDataReducer(state, update, true);

      // Should only have the update data, state should be completely replaced
      expect(result).toHaveLength(1);
      expect(result[0]).toEqual({
        model: "openai:gpt-3.5-turbo",
        cacheCreationInputTokens: 40,
        cacheReadInputTokens: 20,
        inputTokens: 100,
        outputTokens: 80,
      });
    });

    it("should merge when replaceMode is false (default behavior)", () => {
      const state: ModelTokenData[] = [
        {
          model: "anthropic:claude-sonnet-4-0",
          cacheCreationInputTokens: 100,
          cacheReadInputTokens: 50,
          inputTokens: 200,
          outputTokens: 150,
        },
      ];

      const update: ModelTokenData[] = [
        {
          model: "anthropic:claude-sonnet-4-0",
          cacheCreationInputTokens: 25,
          cacheReadInputTokens: 15,
          inputTokens: 75,
          outputTokens: 60,
        },
      ];

      const result = tokenDataReducer(state, update, false);

      expect(result).toHaveLength(1);
      expect(result[0]).toEqual({
        model: "anthropic:claude-sonnet-4-0",
        cacheCreationInputTokens: 125,
        cacheReadInputTokens: 65,
        inputTokens: 275,
        outputTokens: 210,
      });
    });

    it("should replace with empty array when update is empty and replaceMode is true", () => {
      const state: ModelTokenData[] = [
        {
          model: "anthropic:claude-sonnet-4-0",
          cacheCreationInputTokens: 100,
          cacheReadInputTokens: 50,
          inputTokens: 200,
          outputTokens: 150,
        },
      ];

      const result = tokenDataReducer(state, [], true);

      expect(result).toEqual([]);
    });

    it("should return state when update is empty and replaceMode is false", () => {
      const state: ModelTokenData[] = [
        {
          model: "anthropic:claude-sonnet-4-0",
          cacheCreationInputTokens: 100,
          cacheReadInputTokens: 50,
          inputTokens: 200,
          outputTokens: 150,
        },
      ];

      const result = tokenDataReducer(state, [], false);

      expect(result).toEqual(state);
    });

    it("should handle undefined state with replaceMode true", () => {
      const update: ModelTokenData[] = [
        {
          model: "anthropic:claude-sonnet-4-0",
          cacheCreationInputTokens: 100,
          cacheReadInputTokens: 50,
          inputTokens: 200,
          outputTokens: 150,
        },
      ];

      const result = tokenDataReducer(undefined, update, true);

      expect(result).toEqual(update);
    });

    it("should replace state with multiple models when replaceMode is true", () => {
      const state: ModelTokenData[] = [
        {
          model: "anthropic:claude-sonnet-4-0",
          cacheCreationInputTokens: 100,
          cacheReadInputTokens: 50,
          inputTokens: 200,
          outputTokens: 150,
        },
      ];

      const update: ModelTokenData[] = [
        {
          model: "openai:gpt-3.5-turbo",
          cacheCreationInputTokens: 40,
          cacheReadInputTokens: 20,
          inputTokens: 100,
          outputTokens: 80,
        },
        {
          model: "openai:gpt-4",
          cacheCreationInputTokens: 60,
          cacheReadInputTokens: 30,
          inputTokens: 150,
          outputTokens: 120,
        },
      ];

      const result = tokenDataReducer(state, update, true);

      expect(result).toHaveLength(2);
      expect(result).toEqual(update);
    });

    it("should not mutate original state or update arrays", () => {
      const state: ModelTokenData[] = [
        {
          model: "anthropic:claude-sonnet-4-0",
          cacheCreationInputTokens: 100,
          cacheReadInputTokens: 50,
          inputTokens: 200,
          outputTokens: 150,
        },
      ];

      const update: ModelTokenData[] = [
        {
          model: "openai:gpt-3.5-turbo",
          cacheCreationInputTokens: 40,
          cacheReadInputTokens: 20,
          inputTokens: 100,
          outputTokens: 80,
        },
      ];

      const stateCopy = JSON.parse(JSON.stringify(state));
      const updateCopy = JSON.parse(JSON.stringify(update));

      tokenDataReducer(state, update, true);

      expect(state).toEqual(stateCopy);
      expect(update).toEqual(updateCopy);
    });
  });
});

