import { HostProcessPlatform } from "@openswe/shared/hostProcess";
import * as Console from "effect/Console";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";
import * as Terminal from "effect/Terminal";
import { Command, GlobalFlag, Prompt } from "effect/unstable/cli";

import packageJson from "../../package.json" with { type: "json" };
import * as BootService from "../cloud/bootService.ts";
import type * as ServerConfig from "../config.ts";
import * as ProcessRunner from "../processRunner.ts";
import { projectLocationFlags, resolveCliAuthConfig } from "./config.ts";

export const bootServiceLayer = (config: ServerConfig.ServerConfig["Service"]) =>
  BootService.layer({
    baseDir: config.baseDir,
    logsDir: config.logsDir,
    cliVersion: packageJson.version,
  }).pipe(Layer.provide(ProcessRunner.layer));

export type ServiceReconcileResult =
  | {
      readonly changed: false;
      readonly status: BootService.BootServiceStatus;
    }
  | {
      readonly changed: true;
      readonly previouslyInstalled: boolean;
      readonly plan: BootService.BootServicePlan;
    };

/** Install, update, or repair the service using the CLI version running this command. */
export const reconcileService = Effect.fn("cli.service.reconcile")(function* () {
  const service = yield* BootService.BootService;
  const status = yield* service.status;
  if (status.installed && status.current) {
    return { changed: false, status } satisfies ServiceReconcileResult;
  }
  const plan = yield* service.install;
  return {
    changed: true,
    previouslyInstalled: status.installed,
    plan,
  } satisfies ServiceReconcileResult;
});

export function formatServiceStatus(
  status: BootService.BootServiceStatus,
  cliVersion: string,
): string {
  if (!status.supported) {
    return "Open SWE service\n  Status: unavailable on this machine\n  Supported on: Linux with systemd, macOS with launchd";
  }
  if (!status.installed) {
    return "Open SWE service\n  Status: not installed\n  Next: Run `openswe service install`.";
  }
  return [
    "Open SWE service",
    `  Status: ${status.current ? `installed · openswe@${cliVersion}` : "needs an update or repair"}`,
    `  Unit: ${status.unitPath}`,
    `  Logs: ${status.logPath}`,
    ...(status.current ? [] : ["  Next: Run `npx openswe@latest service update`."]),
  ].join("\n");
}

const runServiceCommand = Effect.fn("cli.service.run")(function* <A, E>(
  flags: { readonly baseDir: Parameters<typeof resolveCliAuthConfig>[0]["baseDir"] },
  run: Effect.Effect<A, E, BootService.BootService>,
) {
  const logLevel = yield* GlobalFlag.LogLevel;
  const config = yield* resolveCliAuthConfig(flags, logLevel);
  return yield* run.pipe(Effect.provide(bootServiceLayer(config)));
});

const serviceInstallCommand = Command.make("install", projectLocationFlags).pipe(
  Command.withDescription("Install Open SWE as a background service for this user."),
  Command.withHandler((flags) =>
    runServiceCommand(
      flags,
      Effect.gen(function* () {
        const result = yield* reconcileService();
        if (!result.changed) {
          yield* Console.log(
            `Open SWE service is already installed with openswe@${packageJson.version}.`,
          );
          return;
        }
        yield* Console.log(
          `${result.previouslyInstalled ? "Updated" : "Installed"} Open SWE service with openswe@${packageJson.version}.\nLogs: ${result.plan.logPath}`,
        );
      }),
    ),
  ),
);

const serviceUpdateCommand = Command.make("update", projectLocationFlags).pipe(
  Command.withDescription(
    "Update or repair the background service using this CLI version. Use `npx openswe@latest service update` for the latest release.",
  ),
  Command.withHandler((flags) =>
    runServiceCommand(
      flags,
      Effect.gen(function* () {
        const result = yield* reconcileService();
        if (!result.changed) {
          yield* Console.log(`Open SWE service is already using openswe@${packageJson.version}.`);
          return;
        }
        yield* Console.log(
          `${result.previouslyInstalled ? "Updated" : "Installed"} Open SWE service with openswe@${packageJson.version}.\nLogs: ${result.plan.logPath}`,
        );
      }),
    ),
  ),
);

const serviceUninstallCommand = Command.make("uninstall", projectLocationFlags).pipe(
  Command.withDescription("Stop and remove the Open SWE background service."),
  Command.withHandler((flags) =>
    runServiceCommand(
      flags,
      Effect.gen(function* () {
        const service = yield* BootService.BootService;
        const removed = yield* service.uninstall;
        yield* Console.log(
          removed ? "Removed the Open SWE service." : "Open SWE service is not installed.",
        );
      }),
    ),
  ),
);

const serviceStatusCommand = Command.make("status", projectLocationFlags).pipe(
  Command.withDescription("Show whether the Open SWE background service is installed."),
  Command.withHandler((flags) =>
    runServiceCommand(
      flags,
      Effect.gen(function* () {
        const service = yield* BootService.BootService;
        yield* Console.log(formatServiceStatus(yield* service.status, packageJson.version));
      }),
    ),
  ),
);

export const offerServiceDuringOnboarding = Effect.gen(function* () {
  const service = yield* BootService.BootService;
  const { supported, installed, current } = yield* service.status;
  if (!supported) {
    return false;
  }
  if (installed && current) {
    yield* Console.log("Open SWE is already set up to run in the background on this machine.");
    return true;
  }
  // A LaunchAgent starts at login and dies at logout; there is no
  // enable-linger equivalent on macOS. Do not promise more than that.
  const platform = yield* HostProcessPlatform;
  const wanted = yield* Prompt.run(
    Prompt.confirm({
      message: installed
        ? "The installed Open SWE service needs an update or repair. Update it now?"
        : platform === "darwin"
          ? "Run Open SWE in the background whenever you log in to this Mac? " +
            "It stays reachable through Open SWE Connect while you are logged in."
          : "Run Open SWE in the background whenever this machine boots? " +
            "It stays reachable through Open SWE Connect even after you log out.",
      initial: true,
    }),
  );
  if (!wanted) {
    return false;
  }
  const result = yield* reconcileService();
  if (result.changed) {
    yield* Console.log(
      `Background service ${result.previouslyInstalled ? "updated" : "installed"}. Logs: ${result.plan.logPath}`,
    );
  }
  return true;
});

export const recoverServiceOnboardingOffer = <R>(
  offer: Effect.Effect<boolean, BootService.BootServiceError | Terminal.QuitError, R>,
) =>
  offer.pipe(
    Effect.catchTags({
      QuitError: () => Effect.succeed(false),
      BootServiceUnsupportedError: (error) =>
        Console.log(`Skipping background setup: ${error.message}`).pipe(Effect.as(false)),
      BootServiceCommandError: (error) =>
        Console.warn(`Background setup did not finish: ${error.message}`).pipe(Effect.as(false)),
      BootServiceInstallError: (error) =>
        Console.warn(`Background setup did not finish: ${error.message}`).pipe(Effect.as(false)),
      BootServiceUpdatePendingError: (error) =>
        Console.warn(`Background setup did not finish: ${error.message}`).pipe(Effect.as(false)),
    }),
  );

export const serviceCommand = Command.make("service").pipe(
  Command.withDescription("Manage the Open SWE background service."),
  Command.withSubcommands([
    serviceInstallCommand,
    serviceUninstallCommand,
    serviceUpdateCommand,
    serviceStatusCommand,
  ]),
);
