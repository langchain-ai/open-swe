import { ProviderInstanceEnvironment } from "@openswe/contracts";
import { describe, expect, it } from "@effect/vitest";
import * as Schema from "effect/Schema";

import {
  LangGraphDriver,
  resolveLangGraphDriverConfig,
  resolveLangGraphInstanceRuntime,
} from "./LangGraphDriver.ts";

const decodeEnvironment = Schema.decodeSync(ProviderInstanceEnvironment);

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

  it("merges instance environment without mutating the process environment", () => {
    const baseEnvironment = {
      PATH: "/global/bin",
      OPEN_SWE_MANAGED_SERVER_URL: "https://global.example.test",
      OPEN_SWE_LOCAL_AUTH_TOKEN: "global-placeholder",
    };
    const runtime = resolveLangGraphInstanceRuntime({
      enabled: false,
      config: LangGraphDriver.defaultConfig(),
      environment: decodeEnvironment([
        {
          name: "OPEN_SWE_MANAGED_SERVER_URL",
          value: "https://instance.example.test",
          sensitive: false,
        },
        {
          name: "OPEN_SWE_LOCAL_AUTH_TOKEN",
          value: "instance-placeholder",
          sensitive: true,
        },
      ]),
      baseEnvironment,
    });

    expect(runtime.config.enabled).toBe(true);
    expect(runtime.config.serverUrl).toBe("https://instance.example.test");
    expect(runtime.environment.PATH).toBe("/global/bin");
    expect(runtime.environment.OPEN_SWE_LOCAL_AUTH_TOKEN).toBe("instance-placeholder");
    expect(baseEnvironment.OPEN_SWE_LOCAL_AUTH_TOKEN).toBe("global-placeholder");
  });
});
