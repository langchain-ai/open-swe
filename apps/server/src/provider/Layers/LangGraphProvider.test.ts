import { LangGraphSettings } from "@t3tools/contracts";
import { describe, expect, it } from "@effect/vitest";
import * as Effect from "effect/Effect";
import * as Schema from "effect/Schema";

import {
  buildInitialLangGraphProviderSnapshot,
  LANGGRAPH_BUILT_IN_MODELS,
  langGraphAuthHeaders,
} from "./LangGraphProvider.ts";

const decodeSettings = Schema.decodeSync(LangGraphSettings);

describe("LangGraphProvider", () => {
  it("mirrors the Open SWE model and effort catalog", () => {
    expect(LANGGRAPH_BUILT_IN_MODELS.map((model) => model.slug)).toEqual([
      "anthropic:claude-opus-5",
      "anthropic:claude-sonnet-5",
      "anthropic:claude-fable-5",
      "openai:gpt-5.6-sol",
      "openai:gpt-5.6-terra",
      "openai:gpt-5.6-luna",
      "google_genai:gemini-3.7-flash",
      "fireworks:accounts/fireworks/models/kimi-k3",
      "fireworks:accounts/fireworks/models/deepseek-v4-pro",
      "fireworks:accounts/fireworks/models/glm-5p2",
    ]);
    const sol = LANGGRAPH_BUILT_IN_MODELS.find((model) => model.slug === "openai:gpt-5.6-sol");
    expect(sol?.capabilities?.optionDescriptors).toEqual([
      {
        id: "effort",
        type: "select",
        label: "Reasoning effort",
        description: "Controls how much reasoning Open SWE uses for this model.",
        currentValue: "xhigh",
        options: [
          { id: "none", label: "None" },
          { id: "low", label: "Low" },
          { id: "medium", label: "Medium" },
          { id: "high", label: "High" },
          { id: "xhigh", label: "Extra high", isDefault: true },
        ],
      },
    ]);
  });

  it.effect("advertises plan mode in the provider presentation", () =>
    Effect.gen(function* () {
      const snapshot = yield* buildInitialLangGraphProviderSnapshot(
        decodeSettings({ enabled: true }),
      );
      expect(snapshot.showInteractionModeToggle).toBe(true);
    }),
  );
});

describe("langGraphAuthHeaders", () => {
  it("uses the configured key before the local environment token", () => {
    expect(
      langGraphAuthHeaders(decodeSettings({ apiKey: " configured " }), {
        OPEN_SWE_LOCAL_AUTH_TOKEN: "environment",
      }),
    ).toEqual({
      "x-api-key": "configured",
      authorization: "Bearer configured",
    });
  });

  it("uses the local environment token when no key is configured", () => {
    expect(
      langGraphAuthHeaders(decodeSettings({ apiKey: "" }), {
        OPEN_SWE_LOCAL_AUTH_TOKEN: " local-token ",
      }),
    ).toEqual({
      "x-api-key": "local-token",
      authorization: "Bearer local-token",
    });
  });

  it("omits authentication when neither source contains a key", () => {
    expect(
      langGraphAuthHeaders(decodeSettings({ apiKey: "  " }), {
        OPEN_SWE_LOCAL_AUTH_TOKEN: "  ",
      }),
    ).toEqual({});
  });
});
