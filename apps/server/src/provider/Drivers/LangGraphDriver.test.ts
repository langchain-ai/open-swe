import { describe, expect, it } from "@effect/vitest";

import { LangGraphDriver, resolveLangGraphDriverConfig } from "./LangGraphDriver.ts";

describe("resolveLangGraphDriverConfig", () => {
  it("forces the managed server enabled without changing the input config", () => {
    const config = {
      ...LangGraphDriver.defaultConfig(),
      enabled: false,
      serverUrl: "https://external.example.test",
    };

    const resolved = resolveLangGraphDriverConfig({
      enabled: false,
      config,
      environment: { OPEN_SWE_MANAGED_SERVER_URL: " http://127.0.0.1:2024 " },
    });

    expect(resolved.enabled).toBe(true);
    expect(resolved.serverUrl).toBe("http://127.0.0.1:2024");
    expect(config.enabled).toBe(false);
    expect(config.serverUrl).toBe("https://external.example.test");
  });

  it("preserves external mode when there is no managed URL", () => {
    const config = {
      ...LangGraphDriver.defaultConfig(),
      enabled: false,
      serverUrl: "https://external.example.test",
    };

    expect(resolveLangGraphDriverConfig({ enabled: true, config, environment: {} })).toEqual({
      ...config,
      enabled: true,
    });
  });

  it("ignores a blank managed URL", () => {
    const config = LangGraphDriver.defaultConfig();

    expect(
      resolveLangGraphDriverConfig({
        enabled: false,
        config,
        environment: { OPEN_SWE_MANAGED_SERVER_URL: "   " },
      }),
    ).toEqual({ ...config, enabled: false });
  });
});
