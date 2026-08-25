/**
 * LangGraphProvider — snapshot/health for the Open SWE (LangGraph) driver.
 *
 * Unlike the CLI-backed providers there is no binary to probe and no npm
 * package to compare versions against: the driver attaches to a LangGraph
 * server the user runs themselves. "Installed" therefore means "the
 * configured base URL answers `GET /ok`", and the version comes from the
 * server's own `GET /info`.
 *
 * @module provider/Layers/LangGraphProvider
 */
import {
  type LangGraphSettings,
  type ModelCapabilities,
  type ServerProviderModel,
} from "@t3tools/contracts";
import * as DateTime from "effect/DateTime";
import * as Effect from "effect/Effect";
import * as Option from "effect/Option";
import * as Schema from "effect/Schema";
import { HttpClient, HttpClientRequest } from "effect/unstable/http";
import { createModelCapabilities } from "@t3tools/shared/model";

import {
  buildServerProvider,
  providerModelsFromSettings,
  type ServerProviderDraft,
} from "../providerSnapshot.ts";

const LANGGRAPH_PRESENTATION = {
  displayName: "Open SWE",
  badgeLabel: "Experimental",
  showInteractionModeToggle: true,
  requiresNewThreadForModelChange: false,
} as const;

const EMPTY_CAPABILITIES: ModelCapabilities = createModelCapabilities({
  optionDescriptors: [],
});

function modelCapabilities(efforts: ReadonlyArray<string>, defaultEffort: string) {
  return createModelCapabilities({
    optionDescriptors: [
      {
        id: "effort",
        type: "select",
        label: "Reasoning effort",
        description: "Controls how much reasoning Open SWE uses for this model.",
        currentValue: defaultEffort,
        options: efforts.map((effort) => ({
          id: effort,
          label:
            effort === "xhigh"
              ? "Extra high"
              : `${effort[0]?.toUpperCase() ?? ""}${effort.slice(1)}`,
          ...(effort === defaultEffort ? { isDefault: true } : {}),
        })),
      },
    ],
  });
}

const HEALTH_TIMEOUT_MS = 4_000;

/**
 * Mirrors `SUPPORTED_MODELS` in `agent/dashboard/options.py`. The agent
 * validates the model id server-side, so a stale entry here degrades to a
 * rejected run rather than silent misbehaviour.
 */
export const LANGGRAPH_BUILT_IN_MODELS: ReadonlyArray<ServerProviderModel> = [
  {
    slug: "anthropic:claude-opus-5",
    name: "Opus 5",
    isCustom: false,
    capabilities: modelCapabilities(["low", "medium", "high", "xhigh", "max"], "high"),
  },
  {
    slug: "anthropic:claude-sonnet-5",
    name: "Sonnet 5",
    isCustom: false,
    capabilities: modelCapabilities(["low", "medium", "high", "xhigh", "max"], "high"),
  },
  {
    slug: "anthropic:claude-fable-5",
    name: "Fable 5",
    isCustom: false,
    capabilities: modelCapabilities(["low", "medium", "high", "xhigh", "max"], "high"),
  },
  {
    slug: "openai:gpt-5.6-sol",
    name: "GPT-5.6 Sol",
    isCustom: false,
    capabilities: modelCapabilities(["none", "low", "medium", "high", "xhigh"], "xhigh"),
  },
  {
    slug: "openai:gpt-5.6-terra",
    name: "GPT-5.6 Terra",
    isCustom: false,
    capabilities: modelCapabilities(["none", "low", "medium", "high", "xhigh"], "xhigh"),
  },
  {
    slug: "openai:gpt-5.6-luna",
    name: "GPT-5.6 Luna",
    isCustom: false,
    capabilities: modelCapabilities(["none", "low", "medium", "high", "xhigh", "max"], "xhigh"),
  },
  {
    slug: "google_genai:gemini-3.7-flash",
    name: "Gemini 3.7 Flash",
    isCustom: false,
    capabilities: modelCapabilities(["minimal", "low", "medium", "high"], "medium"),
  },
  {
    slug: "fireworks:accounts/fireworks/models/kimi-k3",
    name: "Kimi K3",
    isCustom: false,
    capabilities: modelCapabilities(["low", "high", "max"], "high"),
  },
  {
    slug: "fireworks:accounts/fireworks/models/deepseek-v4-pro",
    name: "DeepSeek V4 Pro",
    isCustom: false,
    capabilities: modelCapabilities(["none", "low", "medium", "high", "xhigh", "max"], "high"),
  },
  {
    slug: "fireworks:accounts/fireworks/models/glm-5p2",
    name: "GLM 5.2",
    isCustom: false,
    capabilities: modelCapabilities(["none", "high", "max"], "high"),
  },
];

const LangGraphInfoResponse = Schema.Struct({
  version: Schema.optional(Schema.String),
});

function langGraphModels(
  customModels: ReadonlyArray<string> | undefined,
): ReadonlyArray<ServerProviderModel> {
  return providerModelsFromSettings(
    LANGGRAPH_BUILT_IN_MODELS,
    customModels ?? [],
    EMPTY_CAPABILITIES,
  );
}

/** Normalized base URL with any trailing slash removed. */
export function langGraphBaseUrl(settings: LangGraphSettings): string {
  return settings.serverUrl.trim().replace(/\/+$/, "");
}

/**
 * Auth header for the configured server. Returns an empty record when no key
 * is set so a local server needs no configuration at all. The key is never
 * echoed into a snapshot or an event — only into this header.
 */
/**
 * The two LangGraph deployments authenticate differently: a self-hosted
 * server behind `agent.local_auth:auth` wants `Authorization: Bearer`, while
 * LangGraph Cloud wants `x-api-key`. Sending both from the one configured
 * secret keeps a single settings field working against either, and each
 * server ignores the header it does not use.
 */
export function langGraphAuthHeaders(
  settings: LangGraphSettings,
  environment: NodeJS.ProcessEnv = process.env,
): Record<string, string> {
  const key = settings.apiKey.trim() || environment.OPEN_SWE_LOCAL_AUTH_TOKEN?.trim() || "";
  return key.length > 0 ? { "x-api-key": key, authorization: `Bearer ${key}` } : {};
}

export function buildInitialLangGraphProviderSnapshot(
  settings: LangGraphSettings,
): Effect.Effect<ServerProviderDraft> {
  return Effect.gen(function* () {
    const checkedAt = yield* Effect.map(DateTime.now, DateTime.formatIso);
    const models = langGraphModels(settings.customModels);

    if (!settings.enabled) {
      return buildServerProvider({
        presentation: LANGGRAPH_PRESENTATION,
        enabled: false,
        checkedAt,
        models,
        probe: {
          installed: false,
          version: null,
          status: "warning",
          auth: { status: "unknown" },
          message: "Open SWE is disabled in T3 Code settings.",
        },
      });
    }

    return buildServerProvider({
      presentation: LANGGRAPH_PRESENTATION,
      enabled: true,
      checkedAt,
      models,
      probe: {
        installed: true,
        version: null,
        status: "warning",
        auth: { status: "unknown" },
        message: "Contacting the LangGraph server...",
      },
    });
  });
}

export function checkLangGraphProviderStatus(
  settings: LangGraphSettings,
): Effect.Effect<ServerProviderDraft, never, HttpClient.HttpClient> {
  return Effect.gen(function* () {
    const checkedAt = yield* Effect.map(DateTime.now, DateTime.formatIso);
    const models = langGraphModels(settings.customModels);

    if (!settings.enabled) {
      return buildServerProvider({
        presentation: LANGGRAPH_PRESENTATION,
        enabled: false,
        checkedAt,
        models,
        probe: {
          installed: false,
          version: null,
          status: "warning",
          auth: { status: "unknown" },
          message: "Open SWE is disabled in T3 Code settings.",
        },
      });
    }

    const baseUrl = langGraphBaseUrl(settings);
    if (baseUrl.length === 0) {
      return buildServerProvider({
        presentation: LANGGRAPH_PRESENTATION,
        enabled: true,
        checkedAt,
        models,
        probe: {
          installed: false,
          version: null,
          status: "error",
          auth: { status: "unknown" },
          message: "Set a server URL for Open SWE in Settings.",
        },
      });
    }

    const client = yield* HttpClient.HttpClient;
    const headers = langGraphAuthHeaders(settings);

    const health = yield* client
      .execute(HttpClientRequest.get(`${baseUrl}/ok`).pipe(HttpClientRequest.setHeaders(headers)))
      .pipe(
        Effect.timeoutOption(HEALTH_TIMEOUT_MS),
        Effect.orElseSucceed(() => Option.none()),
      );

    if (Option.isNone(health)) {
      return buildServerProvider({
        presentation: LANGGRAPH_PRESENTATION,
        enabled: true,
        checkedAt,
        models,
        probe: {
          installed: false,
          version: null,
          status: "error",
          auth: { status: "unknown" },
          message: `No LangGraph server reachable at ${baseUrl}. Start one with \`make dev\`.`,
        },
      });
    }

    const status = health.value.status;
    if (status === 401 || status === 403) {
      return buildServerProvider({
        presentation: LANGGRAPH_PRESENTATION,
        enabled: true,
        checkedAt,
        models,
        probe: {
          installed: true,
          version: null,
          status: "error",
          auth: { status: "unauthenticated" },
          message: "The LangGraph server rejected the configured API key.",
        },
      });
    }

    if (status < 200 || status >= 300) {
      return buildServerProvider({
        presentation: LANGGRAPH_PRESENTATION,
        enabled: true,
        checkedAt,
        models,
        probe: {
          installed: true,
          version: null,
          status: "error",
          auth: { status: "unknown" },
          message: `LangGraph server at ${baseUrl} answered ${String(status)}.`,
        },
      });
    }

    const version = yield* client
      .execute(HttpClientRequest.get(`${baseUrl}/info`).pipe(HttpClientRequest.setHeaders(headers)))
      .pipe(
        Effect.flatMap((response) => response.json),
        Effect.flatMap(Schema.decodeUnknownEffect(LangGraphInfoResponse)),
        Effect.map((info) => info.version ?? null),
        Effect.timeoutOption(HEALTH_TIMEOUT_MS),
        Effect.orElseSucceed(() => Option.none<string | null>()),
        Effect.map(Option.getOrElse(() => null)),
      );

    return buildServerProvider({
      presentation: LANGGRAPH_PRESENTATION,
      enabled: true,
      checkedAt,
      models,
      probe: {
        installed: true,
        version,
        status: "ready",
        auth: { status: "authenticated" },
      },
    });
  });
}
