import { 
  extractTokenDataFromIssueContent, 
  TOKEN_DATA_OPEN_TAG,
  TOKEN_DATA_CLOSE_TAG
} from "../utils/github/issue-task.js";
import { ModelTokenData } from "@open-swe/shared/open-swe/types";
import { tokenDataReducer } from "@open-swe/shared/caching";

describe("Token Data Persistence End-to-End", () => {
  const mockTokenData: ModelTokenData[] = [
    {
      model: "anthropic:claude-3-sonnet",
      cacheCreationInputTokens: 100,
      cacheReadInputTokens: 50,
      inputTokens: 200,
      outputTokens: 150,
    },
    {
      model: "openai:gpt-4",
      cacheCreationInputTokens: 80,
      cacheReadInputTokens: 30,
      inputTokens: 120,
      outputTokens: 90,
    },
  ];

  describe("extractTokenDataFromIssueContent", () => {
    it("should extract token data from issue content", () => {
      const issueContent = `
# Issue Title

Some issue description here.

${TOKEN_DATA_OPEN_TAG}
${JSON.stringify(mockTokenData, null, 2)}
${TOKEN_DATA_CLOSE_TAG}

More content here.
      `;

      const result = extractTokenDataFromIssueContent(issueContent);
      expect(result).toEqual(mockTokenData);
    });

    it("should return null when no token data tags are present", () => {
      const issueContent = "# Issue without token data";
      const result = extractTokenDataFromIssueContent(issueContent);
      expect(result).toBeNull();
    });

    it("should return null when token data is malformed", () => {
      const issueContent = `
${TOKEN_DATA_OPEN_TAG}
invalid json data
${TOKEN_DATA_CLOSE_TAG}
      `;

      const result = extractTokenDataFromIssueContent(issueContent);
      expect(result).toBeNull();
    });

    it("should handle empty token data array", () => {
      const issueContent = `
${TOKEN_DATA_OPEN_TAG}
[]
${TOKEN_DATA_CLOSE_TAG}
      `;

      const result = extractTokenDataFromIssueContent(issueContent);
      expect(result).toEqual([]);
    });
  });

  describe("Token Data Reducer with Replace Mode", () => {
    it("should replace token data when extracted from issue", () => {
      const existingState: ModelTokenData[] = [
        {
          model: "old-model",
          cacheCreationInputTokens: 1000,
          cacheReadInputTokens: 500,
          inputTokens: 2000,
          outputTokens: 1500,
        },
      ];

      // Simulate reading from issue with replace mode
      const result = tokenDataReducer(existingState, mockTokenData, true);
      
      expect(result).toEqual(mockTokenData);
      expect(result).not.toContainEqual(existingState[0]);
    });

    it("should merge token data during normal operation", () => {
      const existingState: ModelTokenData[] = [
        {
          model: "anthropic:claude-3-sonnet",
          cacheCreationInputTokens: 100,
          cacheReadInputTokens: 50,
          inputTokens: 200,
          outputTokens: 150,
        },
      ];

      const newData: ModelTokenData[] = [
        {
          model: "anthropic:claude-3-sonnet",
          cacheCreationInputTokens: 50,
          cacheReadInputTokens: 25,
          inputTokens: 100,
          outputTokens: 75,
        },
      ];

      const result = tokenDataReducer(existingState, newData, false);
      
      expect(result).toHaveLength(1);
      expect(result[0]).toEqual({
        model: "anthropic:claude-3-sonnet",
        cacheCreationInputTokens: 150,
        cacheReadInputTokens: 75,
        inputTokens: 300,
        outputTokens: 225,
      });
    });
  });

  describe("Integration Flow", () => {
    it("should handle the complete flow: persist -> extract -> replace", () => {
      // Step 1: Start with some token data
      const initialTokenData: ModelTokenData[] = [
        {
          model: "anthropic:claude-3-sonnet",
          cacheCreationInputTokens: 100,
          cacheReadInputTokens: 50,
          inputTokens: 200,
          outputTokens: 150,
        },
      ];

      // Step 2: Simulate persisting to issue (create issue content)
      const issueContent = `
# Test Issue

${TOKEN_DATA_OPEN_TAG}
${JSON.stringify(initialTokenData, null, 2)}
${TOKEN_DATA_CLOSE_TAG}
      `;

      // Step 3: Extract token data from issue
      const extractedData = extractTokenDataFromIssueContent(issueContent);
      expect(extractedData).toEqual(initialTokenData);

      // Step 4: Initialize new graph state with extracted data (replace mode)
      const newGraphState: ModelTokenData[] = [];
      const initializedState = tokenDataReducer(newGraphState, extractedData!, true);
      expect(initializedState).toEqual(initialTokenData);

      // Step 5: Accumulate more token data during graph execution
      const additionalData: ModelTokenData[] = [
        {
          model: "anthropic:claude-3-sonnet",
          cacheCreationInputTokens: 50,
          cacheReadInputTokens: 25,
          inputTokens: 100,
          outputTokens: 75,
        },
      ];

      const accumulatedState = tokenDataReducer(initializedState, additionalData, false);
      expect(accumulatedState).toHaveLength(1);
      expect(accumulatedState[0]).toEqual({
        model: "anthropic:claude-3-sonnet",
        cacheCreationInputTokens: 150,
        cacheReadInputTokens: 75,
        inputTokens: 300,
        outputTokens: 225,
      });

      // Step 6: Simulate persisting accumulated data back to issue
      const updatedIssueContent = `
# Test Issue

${TOKEN_DATA_OPEN_TAG}
${JSON.stringify(accumulatedState, null, 2)}
${TOKEN_DATA_CLOSE_TAG}
      `;

      // Step 7: Verify the updated data can be extracted correctly
      const finalExtractedData = extractTokenDataFromIssueContent(updatedIssueContent);
      expect(finalExtractedData).toEqual(accumulatedState);
    });

    it("should handle multiple models in the persistence flow", () => {
      const multiModelData: ModelTokenData[] = [
        {
          model: "anthropic:claude-3-sonnet",
          cacheCreationInputTokens: 100,
          cacheReadInputTokens: 50,
          inputTokens: 200,
          outputTokens: 150,
        },
        {
          model: "openai:gpt-4",
          cacheCreationInputTokens: 80,
          cacheReadInputTokens: 30,
          inputTokens: 120,
          outputTokens: 90,
        },
        {
          model: "openai:gpt-3.5-turbo",
          cacheCreationInputTokens: 40,
          cacheReadInputTokens: 20,
          inputTokens: 60,
          outputTokens: 45,
        },
      ];

      // Create issue content with multiple models
      const issueContent = `
${TOKEN_DATA_OPEN_TAG}
${JSON.stringify(multiModelData, null, 2)}
${TOKEN_DATA_CLOSE_TAG}
      `;

      // Extract and verify
      const extractedData = extractTokenDataFromIssueContent(issueContent);
      expect(extractedData).toEqual(multiModelData);
      expect(extractedData).toHaveLength(3);

      // Initialize new state with replace mode
      const newState = tokenDataReducer([], extractedData!, true);
      expect(newState).toEqual(multiModelData);

      // Add more data for one model
      const additionalData: ModelTokenData[] = [
        {
          model: "openai:gpt-4",
          cacheCreationInputTokens: 20,
          cacheReadInputTokens: 10,
          inputTokens: 30,
          outputTokens: 25,
        },
      ];

      const mergedState = tokenDataReducer(newState, additionalData, false);
      expect(mergedState).toHaveLength(3);
      
      const gpt4Model = mergedState.find(m => m.model === "openai:gpt-4");
      expect(gpt4Model).toEqual({
        model: "openai:gpt-4",
        cacheCreationInputTokens: 100,
        cacheReadInputTokens: 40,
        inputTokens: 150,
        outputTokens: 115,
      });
    });
  });

  describe("Edge Cases", () => {
    it("should handle token data at different positions in issue content", () => {
      const issueContent = `
# Issue Title

Some content before.

<details>
<summary>Other details</summary>
Some other content
</details>

${TOKEN_DATA_OPEN_TAG}
${JSON.stringify(mockTokenData, null, 2)}
${TOKEN_DATA_CLOSE_TAG}

<details>
<summary>More details</summary>
More content
</details>
      `;

      const result = extractTokenDataFromIssueContent(issueContent);
      expect(result).toEqual(mockTokenData);
    });

    it("should handle very large token counts", () => {
      const largeTokenData: ModelTokenData[] = [
        {
          model: "anthropic:claude-3-opus",
          cacheCreationInputTokens: 1000000,
          cacheReadInputTokens: 500000,
          inputTokens: 2000000,
          outputTokens: 1500000,
        },
      ];

      const issueContent = `
${TOKEN_DATA_OPEN_TAG}
${JSON.stringify(largeTokenData, null, 2)}
${TOKEN_DATA_CLOSE_TAG}
      `;

      const result = extractTokenDataFromIssueContent(issueContent);
      expect(result).toEqual(largeTokenData);
    });

    it("should preserve token data structure exactly", () => {
      const preciseTokenData: ModelTokenData[] = [
        {
          model: "test-model",
          cacheCreationInputTokens: 123,
          cacheReadInputTokens: 456,
          inputTokens: 789,
          outputTokens: 101112,
        },
      ];

      const issueContent = `
${TOKEN_DATA_OPEN_TAG}
${JSON.stringify(preciseTokenData, null, 2)}
${TOKEN_DATA_CLOSE_TAG}
      `;

      const extracted = extractTokenDataFromIssueContent(issueContent);
      expect(extracted).toEqual(preciseTokenData);
      
      // Verify exact values
      expect(extracted![0].cacheCreationInputTokens).toBe(123);
      expect(extracted![0].cacheReadInputTokens).toBe(456);
      expect(extracted![0].inputTokens).toBe(789);
      expect(extracted![0].outputTokens).toBe(101112);
    });
  });
});

