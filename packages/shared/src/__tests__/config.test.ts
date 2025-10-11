import { getCustomConfigurableFields } from "../open-swe/utils/config.js";
import { GraphConfig } from "../open-swe/types.js";

describe("getCustomConfigurableFields", () => {
  it("should include apiKeys even though they are hidden", () => {
    const config = {
      configurable: {
        apiKeys: {
          anthropicApiKey: "encrypted-key-123",
          openaiApiKey: "encrypted-key-456",
        },
        plannerModelName: "anthropic:claude-sonnet-4-0",
      },
    } as unknown as GraphConfig;

    const result = getCustomConfigurableFields(config);

    expect(result?.apiKeys).toEqual({
      anthropicApiKey: "encrypted-key-123",
      openaiApiKey: "encrypted-key-456",
    });
  });

  it("should include langgraph internal fields like langgraph_auth_user", () => {
    const config = {
      configurable: {
        apiKeys: {
          anthropicApiKey: "encrypted-key-123",
        },
        plannerModelName: "anthropic:claude-sonnet-4-0",
        langgraph_auth_user: {
          display_name: "testuser",
          identity: "user-123",
        },
      },
    } as unknown as GraphConfig;

    const result = getCustomConfigurableFields(config);

    expect((result as any).langgraph_auth_user).toEqual({
      display_name: "testuser",
      identity: "user-123",
    });
    expect(result?.apiKeys).toEqual({
      anthropicApiKey: "encrypted-key-123",
    });
  });

  it("should include all langgraph_ prefixed fields", () => {
    const config = {
      configurable: {
        plannerModelName: "anthropic:claude-sonnet-4-0",
        langgraph_auth_user: { display_name: "user1" },
        langgraph_custom_field: "value",
        langgraph_another: 123,
      },
    } as unknown as GraphConfig;

    const result = getCustomConfigurableFields(config) as any;

    expect(result.langgraph_auth_user).toEqual({ display_name: "user1" });
    expect(result.langgraph_custom_field).toBe("value");
    expect(result.langgraph_another).toBe(123);
  });

  it("should not include GitHub tokens and other truly hidden fields", () => {
    const config = {
      configurable: {
        "x-github-access-token": "token-123",
        "x-github-installation-token": "install-token-456",
        apiKeys: {
          anthropicApiKey: "encrypted-key-123",
        },
      },
    } as unknown as GraphConfig;

    const result = getCustomConfigurableFields(config) as any;

    expect(result["x-github-access-token"]).toBeUndefined();
    expect(result["x-github-installation-token"]).toBeUndefined();
    expect(result.apiKeys).toEqual({
      anthropicApiKey: "encrypted-key-123",
    });
  });

  it("should include customFramework even though it is hidden", () => {
    const config = {
      configurable: {
        customFramework: true,
      },
    } as unknown as GraphConfig;

    const result = getCustomConfigurableFields(config);

    expect(result?.customFramework).toBe(true);
  });

  it("should include reviewPullNumber even though it is hidden", () => {
    const config = {
      configurable: {
        reviewPullNumber: 123,
      },
    } as unknown as GraphConfig;

    const result = getCustomConfigurableFields(config);

    expect(result?.reviewPullNumber).toBe(123);
  });
});

