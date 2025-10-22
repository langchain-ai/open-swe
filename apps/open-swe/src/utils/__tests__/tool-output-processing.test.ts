import { jest } from "@jest/globals";
import type { GraphState } from "@openswe/shared/open-swe/types";

jest.unstable_mockModule("../mcp-output/index.js", () => ({
  handleMcpDocumentationOutput: jest.fn(async () => "processed"),
}));

const toolOutputModule = await import("../tool-output-processing.js");
const mcpOutputModule = (await import("../mcp-output/index.js")) as {
  handleMcpDocumentationOutput: jest.Mock;
};

const {
  DOCUMENT_CACHE_CHARACTER_BUDGET,
  enforceDocumentCacheBudget,
  processToolCallContent,
  toolOutputProcessingLogger,
} = toolOutputModule;
const { handleMcpDocumentationOutput } = mcpOutputModule;

describe("enforceDocumentCacheBudget", () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("returns original content when within budget without logging", () => {
    const warnSpy = jest.spyOn(toolOutputProcessingLogger, "warn");
    const content = "a".repeat(DOCUMENT_CACHE_CHARACTER_BUDGET - 1);

    const result = enforceDocumentCacheBudget(content);

    expect(result).toEqual({ content, truncated: false });
    expect(warnSpy).not.toHaveBeenCalled();
  });

  it("truncates and logs when over budget", () => {
    const warnSpy = jest.spyOn(toolOutputProcessingLogger, "warn");
    const content = "a".repeat(DOCUMENT_CACHE_CHARACTER_BUDGET + 1000);

    const result = enforceDocumentCacheBudget(content);

    expect(result.truncated).toBe(true);
    expect(result.content.length).toBeLessThanOrEqual(
      DOCUMENT_CACHE_CHARACTER_BUDGET,
    );
    expect(warnSpy).toHaveBeenCalled();
  });
});

describe("processToolCallContent", () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("caps cached payloads for higher-context tools", async () => {
    const warnSpy = jest.spyOn(toolOutputProcessingLogger, "warn");
    const longResult = "b".repeat(DOCUMENT_CACHE_CHARACTER_BUDGET + 5000);
    const toolCall = {
      name: "docs",
      args: { url: "https://example.com" },
    };
    const state: Pick<GraphState, "documentCache"> = {
      documentCache: {},
    };

    const { stateUpdates, content } = await processToolCallContent(
      toolCall,
      longResult,
      {
        higherContextLimitToolNames: ["docs"],
        state,
        config: {} as any,
      },
    );

    expect(handleMcpDocumentationOutput).toHaveBeenCalledTimes(1);
    const [payload, , options] = handleMcpDocumentationOutput.mock.calls[0];
    expect(payload).toBe(longResult);
    expect(options).toEqual({ url: "https://example.com/" });
    expect(content).toBe("processed");
    expect(stateUpdates).toBeDefined();
    const cachedValue = stateUpdates?.documentCache?.["https://example.com/"];
    expect(cachedValue).toBeDefined();
    expect(cachedValue?.length).toBeLessThanOrEqual(
      DOCUMENT_CACHE_CHARACTER_BUDGET,
    );
    expect(warnSpy).toHaveBeenCalled();
  });
});
