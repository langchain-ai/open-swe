/**
 * LangGraphDriver — Open SWE's LangGraph agent as a Open SWE provider.
 *
 * Attaches to either an externally configured LangGraph server or the
 * desktop-managed Open SWE runtime advertised through the process environment.
 * The managed runtime is an in-memory override and never rewrites user settings.
 *
 * @module provider/Drivers/LangGraphDriver
 */
import {
  LangGraphSettings,
  ProviderDriverKind,
  type ProviderInstanceEnvironment,
  type ServerProvider,
} from "@openswe/contracts";
import * as Effect from "effect/Effect";
import * as FileSystem from "effect/FileSystem";
import * as Path from "effect/Path";
import * as Schema from "effect/Schema";
import { HttpClient } from "effect/unstable/http";

import * as BackgroundPolicy from "../../background/BackgroundPolicy.ts";
import { ServerConfig } from "../../config.ts";
import { ServerSettingsService } from "../../serverSettings.ts";
import { ProviderDriverError } from "../Errors.ts";
import { makeLangGraphTextGeneration } from "../../textGeneration/LangGraphTextGeneration.ts";
import { makeLangGraphAdapter } from "../Layers/LangGraphAdapter.ts";
import { ProviderEventLoggers } from "../Layers/ProviderEventLoggers.ts";
import {
  buildInitialLangGraphProviderSnapshot,
  checkLangGraphProviderStatus,
} from "../Layers/LangGraphProvider.ts";
import { makeManagedServerProvider } from "../makeManagedServerProvider.ts";
import {
  defaultProviderContinuationIdentity,
  type ProviderDriver,
  type ProviderInstance,
} from "../ProviderDriver.ts";
import type { ServerProviderDraft } from "../providerSnapshot.ts";
import { mergeProviderInstanceEnvironment } from "../ProviderInstanceEnvironment.ts";
import {
  makeManualOnlyProviderMaintenanceCapabilities,
  makeStaticProviderMaintenanceResolver,
  resolveProviderMaintenanceCapabilitiesEffect,
} from "../providerMaintenance.ts";
import {
  haveProviderSnapshotSettingsChanged,
  makeProviderSnapshotSettingsSource,
  type ProviderSnapshotSettings,
} from "../providerUpdateSettings.ts";

const decodeLangGraphSettings = Schema.decodeSync(LangGraphSettings);

const DRIVER_KIND = ProviderDriverKind.make("langgraph");

export function resolveLangGraphDriverConfig(input: {
  readonly enabled: boolean;
  readonly config: LangGraphSettings;
  readonly environment?: NodeJS.ProcessEnv;
}): LangGraphSettings {
  const managedServerUrl = (input.environment ?? process.env).OPEN_SWE_MANAGED_SERVER_URL?.trim();
  if (managedServerUrl) {
    return {
      ...input.config,
      enabled: true,
      serverUrl: managedServerUrl,
    };
  }
  return { ...input.config, enabled: input.enabled };
}

export function resolveLangGraphInstanceRuntime(input: {
  readonly enabled: boolean;
  readonly config: LangGraphSettings;
  readonly environment: ProviderInstanceEnvironment;
  readonly baseEnvironment?: NodeJS.ProcessEnv;
}): { readonly config: LangGraphSettings; readonly environment: NodeJS.ProcessEnv } {
  const environment = mergeProviderInstanceEnvironment(input.environment, input.baseEnvironment);
  return {
    config: resolveLangGraphDriverConfig({
      enabled: input.enabled,
      config: input.config,
      environment,
    }),
    environment,
  };
}

// Nothing to update: the server is not a binary this app installs.
const UPDATE = makeStaticProviderMaintenanceResolver(
  makeManualOnlyProviderMaintenanceCapabilities({
    provider: DRIVER_KIND,
    packageName: null,
  }),
);

export type LangGraphDriverEnv =
  | BackgroundPolicy.BackgroundPolicy
  | FileSystem.FileSystem
  | HttpClient.HttpClient
  | Path.Path
  | ProviderEventLoggers
  | ServerConfig
  | ServerSettingsService;

const withInstanceIdentity =
  (input: {
    readonly instanceId: ProviderInstance["instanceId"];
    readonly displayName: string | undefined;
    readonly accentColor: string | undefined;
    readonly continuationGroupKey: string;
  }) =>
  (snapshot: ServerProviderDraft): ServerProvider => ({
    ...snapshot,
    instanceId: input.instanceId,
    driver: DRIVER_KIND,
    ...(input.displayName ? { displayName: input.displayName } : {}),
    ...(input.accentColor ? { accentColor: input.accentColor } : {}),
    continuation: { groupKey: input.continuationGroupKey },
  });

export const LangGraphDriver: ProviderDriver<LangGraphSettings, LangGraphDriverEnv> = {
  driverKind: DRIVER_KIND,
  metadata: {
    displayName: "Open SWE",
    supportsMultipleInstances: true,
  },
  configSchema: LangGraphSettings,
  defaultConfig: (): LangGraphSettings => decodeLangGraphSettings({}),
  create: ({ instanceId, displayName, accentColor, environment, enabled, config }) =>
    Effect.gen(function* () {
      const httpClient = yield* HttpClient.HttpClient;
      const fileSystem = yield* FileSystem.FileSystem;
      const pathService = yield* Path.Path;
      const eventLoggers = yield* ProviderEventLoggers;
      const serverConfig = yield* ServerConfig;
      const serverSettings = yield* ServerSettingsService;
      const continuationIdentity = defaultProviderContinuationIdentity({
        driverKind: DRIVER_KIND,
        instanceId,
      });
      const stampIdentity = withInstanceIdentity({
        instanceId,
        displayName,
        accentColor,
        continuationGroupKey: continuationIdentity.continuationKey,
      });
      const runtime = resolveLangGraphInstanceRuntime({ enabled, config, environment });
      const effectiveConfig = runtime.config;
      const processEnv = runtime.environment;
      const maintenanceCapabilities = yield* resolveProviderMaintenanceCapabilitiesEffect(UPDATE, {
        binaryPath: "",
        env: processEnv,
      });

      const adapter = yield* makeLangGraphAdapter(effectiveConfig, instanceId, {
        attachmentsDir: serverConfig.attachmentsDir,
        environment: processEnv,
        ...(eventLoggers.native ? { nativeEventLogger: eventLoggers.native } : {}),
      }).pipe(
        Effect.provideService(HttpClient.HttpClient, httpClient),
        Effect.provideService(FileSystem.FileSystem, fileSystem),
        Effect.provideService(Path.Path, pathService),
      );
      const textGeneration = yield* makeLangGraphTextGeneration(effectiveConfig, processEnv).pipe(
        Effect.provideService(HttpClient.HttpClient, httpClient),
      );

      const checkProvider = checkLangGraphProviderStatus(effectiveConfig, processEnv).pipe(
        Effect.map(stampIdentity),
        Effect.provideService(HttpClient.HttpClient, httpClient),
      );

      const snapshotSettings = makeProviderSnapshotSettingsSource(effectiveConfig, serverSettings);
      const snapshot = yield* makeManagedServerProvider<
        ProviderSnapshotSettings<LangGraphSettings>
      >({
        maintenanceCapabilities,
        getSettings: snapshotSettings.getSettings,
        streamSettings: snapshotSettings.streamSettings,
        haveSettingsChanged: haveProviderSnapshotSettingsChanged,
        initialSnapshot: (settings) =>
          buildInitialLangGraphProviderSnapshot(settings.provider).pipe(Effect.map(stampIdentity)),
        checkProvider,
      }).pipe(
        Effect.mapError(
          (cause) =>
            new ProviderDriverError({
              driver: DRIVER_KIND,
              instanceId,
              detail: `Failed to build Open SWE snapshot: ${cause.message ?? String(cause)}`,
              cause,
            }),
        ),
      );

      return {
        instanceId,
        driverKind: DRIVER_KIND,
        continuationIdentity,
        displayName,
        accentColor,
        enabled: effectiveConfig.enabled,
        snapshot,
        adapter,
        textGeneration,
      } satisfies ProviderInstance;
    }),
};
