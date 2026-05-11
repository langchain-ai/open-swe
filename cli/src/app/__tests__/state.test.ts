import { describe, it, expect, beforeEach } from "vitest";
import { useStore } from "../store.js";

const initialState = useStore.getState();

describe("Zustand Store", () => {
  beforeEach(() => {
    useStore.setState(initialState, true);
  });

  it("should reset messages to the initial seed message", () => {
    useStore.getState().addMessage({
      author: "user",
      chunks: [{ kind: "text", text: "Some message" }],
    });
    expect(useStore.getState().messages.length).toBeGreaterThan(1);

    useStore.getState().resetMessages();
    const { messages } = useStore.getState();
    expect(messages).toHaveLength(1);
    expect(messages[0].author).toBe("system");
    expect(messages[0].chunks[0].kind).toBe("text");
  });

  it("should add a new message to the state", () => {
    const initialMessagesCount = useStore.getState().messages.length;

    const newMessage = {
      author: "user" as const,
      chunks: [{ kind: "text" as const, text: "Hello, world!" }],
    };

    useStore.getState().addMessage(newMessage);

    const { messages } = useStore.getState();
    expect(messages).toHaveLength(initialMessagesCount + 1);
    expect(messages[messages.length - 1].chunks[0].text).toBe("Hello, world!");
    expect(messages[messages.length - 1].author).toBe("user");
    expect(messages[messages.length - 1].id).toBeDefined();
  });

  it("should correctly update a tool execution chunk", () => {
    const toolCallId = "test-tool-call-id";
    const output = "Tool execution completed successfully";

    useStore.getState().addMessage({
      author: "agent",
      chunks: [
        {
          kind: "tool-execution",
          toolCallId,
          toolName: "test_tool",
          toolArgs: { arg1: "value1" },
          status: "running",
          output: "",
        },
      ],
    });

    useStore
      .getState()
      .updateToolExecution({ toolCallId, status: "success", output });

    const { messages } = useStore.getState();
    const lastMessage = messages[messages.length - 1];
    const updatedChunk = lastMessage.chunks[0];

    expect(updatedChunk.kind).toBe("tool-execution");
    expect(updatedChunk.status).toBe("success");
    expect(updatedChunk.output).toBe(output);
  });

  it("should set and clear API keys", () => {
    expect(useStore.getState().apiKeys).toEqual({});

    const testKey = "sk-test-12345";
    useStore.getState().setApiKey("openai", testKey);
    expect(useStore.getState().apiKeys.openai).toBe(testKey);

    useStore.getState().setApiKey("anthropic", "sk-ant-12345");
    expect(useStore.getState().apiKeys.anthropic).toBe("sk-ant-12345");

    useStore.getState().clearApiKeys();
    expect(useStore.getState().apiKeys).toEqual({});
  });

  it("should set all API keys at once", () => {
    const apiKeys = { openai: "sk-1", anthropic: "sk-2", google: "sk-3" };
    useStore.getState().setApiKeys(apiKeys);
    expect(useStore.getState().apiKeys).toEqual(apiKeys);
  });

  it("should set and get model configuration", () => {
    const newConfig = {
      name: "gpt-4",
      provider: "openai" as const,
      effort: "high" as const,
    };

    useStore.getState().setModelConfig(newConfig);
    expect(useStore.getState().modelConfig).toEqual(newConfig);
  });

  it("should manage busy state", () => {
    expect(useStore.getState().busy).toBe(false);

    useStore.getState().setBusy(true);
    expect(useStore.getState().busy).toBe(true);

    useStore.getState().setBusy(false);
    expect(useStore.getState().busy).toBe(false);
  });

  it("should toggle blink state", () => {
    const initialBlink = useStore.getState().blink;

    useStore.getState().toggleBlink();
    expect(useStore.getState().blink).toBe(!initialBlink);

    useStore.getState().toggleBlink();
    expect(useStore.getState().blink).toBe(initialBlink);
  });
});
