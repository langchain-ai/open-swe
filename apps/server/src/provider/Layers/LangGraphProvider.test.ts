import { LangGraphSettings } from "@openswe/contracts";
import { describe, expect, it } from "@effect/vitest";
import * as Effect from "effect/Effect";
import * as Schema from "effect/Schema";
import { HttpClient, HttpClientResponse } from "effect/unstable/http";

import {
  buildInitialLangGraphProviderSnapshot,
  checkLangGraphProviderStatus,
  LANGGRAPH_BUILT_IN_MODELS,
  langGraphAuthHeaders,
  parseLangGraphCapabilities,
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

describe("LangGraph capability discovery", () => {
  it("normalizes discovered models, skills, slash commands, and custom models", () => {
    const discovery = parseLangGraphCapabilities(
      {
        models: [
          {
            id: " provider:new-model ",
            label: " New Model ",
            efforts: ["low", "high", "high", ""],
            default_effort: "unsupported",
            supports_images: true,
          },
          {
            id: "provider:new-model",
            label: "Duplicate",
            efforts: ["high"],
            default_effort: "high",
          },
        ],
        skills: [
          { name: "baby-sit", description: " Monitor CI ", enabled: true },
          { name: "BABY-SIT", description: "duplicate", enabled: true },
          { name: "../unsafe", description: "unsafe", enabled: true },
        ],
        slash_commands: [
          { name: "/baby-sit", description: " Monitor CI ", input: { hint: " PR URL " } },
          { name: "BABY-SIT", description: "duplicate" },
        ],
      },
      ["provider:custom"],
    );

    expect(discovery.models.map((model) => model.slug)).toEqual([
      "provider:new-model",
      "provider:custom",
    ]);
    expect(discovery.models[0]?.capabilities?.optionDescriptors?.[0]).toMatchObject({
      id: "effort",
      currentValue: "low",
      options: [
        { id: "low", label: "Low", isDefault: true },
        { id: "high", label: "High" },
      ],
    });
    expect(discovery.skills).toEqual([
      {
        name: "baby-sit",
        description: "Monitor CI",
        path: "/bundled-skills/baby-sit/SKILL.md",
        scope: "bundled",
        enabled: true,
      },
    ]);
    expect(discovery.slashCommands).toEqual([
      {
        name: "baby-sit",
        description: "Monitor CI",
        input: { hint: "PR URL" },
      },
    ]);
  });

  it.effect("uses authenticated dynamic capabilities without exposing the key", () =>
    Effect.gen(function* () {
      const requested: Array<{ url: string; authorization: string | undefined }> = [];
      const client = HttpClient.make((request) => {
        requested.push({ url: request.url, authorization: request.headers["authorization"] });
        const response = request.url.endsWith("/ok")
          ? new Response(null, { status: 200 })
          : request.url.endsWith("/info")
            ? Response.json({ version: "1.2.3" })
            : Response.json({
                models: [
                  {
                    id: "provider:dynamic",
                    label: "Dynamic",
                    efforts: ["low", "high"],
                    default_effort: "high",
                    supports_images: true,
                  },
                ],
                skills: [{ name: "baby-sit", description: "Monitor CI", enabled: true }],
                slash_commands: [{ name: "baby-sit", description: "Monitor CI" }],
              });
        return Effect.succeed(HttpClientResponse.fromWeb(request, response));
      });

      const snapshot = yield* checkLangGraphProviderStatus(
        decodeSettings({ serverUrl: "https://example.test" }),
        { OPEN_SWE_LOCAL_AUTH_TOKEN: "instance-placeholder" },
      ).pipe(Effect.provideService(HttpClient.HttpClient, client));

      expect(snapshot.status).toBe("ready");
      expect(snapshot.version).toBe("1.2.3");
      expect(snapshot.models.map((model) => model.slug)).toEqual(["provider:dynamic"]);
      expect(snapshot.skills.map((skill) => skill.name)).toEqual(["baby-sit"]);
      expect(snapshot.slashCommands.map((command) => command.name)).toEqual(["baby-sit"]);
      expect(
        requested.every((request) => request.authorization === "Bearer instance-placeholder"),
      ).toBe(true);
      expect(snapshot.message).toBeUndefined();
      expect(snapshot.auth).toEqual({ status: "authenticated" });
    }),
  );

  it.effect("falls back through legacy options and then built-ins without failing health", () =>
    Effect.gen(function* () {
      let legacyValid = true;
      const client = HttpClient.make((request) => {
        if (request.url.endsWith("/ok")) {
          return Effect.succeed(
            HttpClientResponse.fromWeb(request, new Response(null, { status: 200 })),
          );
        }
        if (request.url.endsWith("/info")) {
          return Effect.succeed(HttpClientResponse.fromWeb(request, Response.json({})));
        }
        if (request.url.endsWith("/provider-capabilities")) {
          return Effect.succeed(
            HttpClientResponse.fromWeb(request, new Response(null, { status: 404 })),
          );
        }
        return Effect.succeed(
          HttpClientResponse.fromWeb(
            request,
            Response.json(
              legacyValid
                ? {
                    models: [
                      {
                        id: "provider:legacy",
                        label: "Legacy",
                        efforts: ["medium"],
                        default_effort: "medium",
                      },
                    ],
                  }
                : { models: "invalid" },
            ),
          ),
        );
      });
      const settings = decodeSettings({ serverUrl: "https://example.test" });

      const legacy = yield* checkLangGraphProviderStatus(settings).pipe(
        Effect.provideService(HttpClient.HttpClient, client),
      );
      legacyValid = false;
      const fallback = yield* checkLangGraphProviderStatus(settings).pipe(
        Effect.provideService(HttpClient.HttpClient, client),
      );

      expect(legacy.status).toBe("ready");
      expect(legacy.models.map((model) => model.slug)).toEqual(["provider:legacy"]);
      expect(fallback.status).toBe("ready");
      expect(fallback.models.map((model) => model.slug)).toEqual(
        LANGGRAPH_BUILT_IN_MODELS.map((model) => model.slug),
      );
      expect(fallback.skills).toEqual([]);
      expect(fallback.slashCommands).toEqual([]);
    }),
  );
});
