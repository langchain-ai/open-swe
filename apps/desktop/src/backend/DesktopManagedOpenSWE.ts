import * as Context from "effect/Context";
import * as Crypto from "effect/Crypto";
import * as Duration from "effect/Duration";
import * as Effect from "effect/Effect";
import * as Encoding from "effect/Encoding";
import * as FileSystem from "effect/FileSystem";
import * as Layer from "effect/Layer";
import * as Schedule from "effect/Schedule";
import * as Schema from "effect/Schema";
import * as Scope from "effect/Scope";
import { HttpClient, HttpClientRequest } from "effect/unstable/http";
import { ChildProcess, ChildProcessSpawner } from "effect/unstable/process";

import * as NetService from "@t3tools/shared/Net";

import * as DesktopEnvironment from "../app/DesktopEnvironment.ts";

const START_PORT = 2024;
const MAX_PORT = 65_535;
const READINESS_TIMEOUT = Duration.minutes(1);
const READINESS_INTERVAL = Duration.millis(100);

const MANAGED_ENV_KEYS = [
  "OPEN_SWE_MANAGED_SERVER_URL",
  "OPEN_SWE_LOCAL_AUTH_TOKEN",
  "OPEN_SWE_LOCAL_PROJECTS_FILE",
] as const;

type ManagedEnvKey = (typeof MANAGED_ENV_KEYS)[number];

export class DesktopManagedOpenSWEError extends Schema.TaggedErrorClass<DesktopManagedOpenSWEError>()(
  "DesktopManagedOpenSWEError",
  {
    stage: Schema.String,
    cause: Schema.Defect(),
  },
) {
  override get message(): string {
    return `Managed Open SWE failed during ${this.stage}.`;
  }
}

export interface ManagedOpenSWECommandInput {
  readonly pythonPath: string;
  readonly configPath: string;
  readonly cwd: string;
  readonly port: number;
  readonly token: string;
  readonly projectsFile: string;
  readonly artifactsDir: string;
  readonly inheritedEnv?: NodeJS.ProcessEnv;
}

export function makeManagedOpenSWECommand(input: ManagedOpenSWECommandInput): ChildProcess.Command {
  return ChildProcess.make(
    input.pythonPath,
    [
      "-c",
      "from langgraph_cli.cli import cli; cli()",
      "dev",
      "--no-reload",
      "--no-browser",
      "--host",
      "127.0.0.1",
      "--port",
      String(input.port),
      "--config",
      input.configPath,
    ],
    {
      cwd: input.cwd,
      env: {
        ...(input.inheritedEnv ?? process.env),
        PYTHONDONTWRITEBYTECODE: "1",
        OPEN_SWE_LOCAL_AUTH_TOKEN: input.token,
        OPEN_SWE_LOCAL_PROJECTS_FILE: input.projectsFile,
        OPEN_SWE_LOCAL_ARTIFACTS_DIR: input.artifactsDir,
      },
      extendEnv: false,
      stdin: "ignore",
      stdout: "inherit",
      stderr: "inherit",
      killSignal: "SIGTERM",
      forceKillAfter: Duration.seconds(2),
    },
  );
}

export class DesktopManagedOpenSWE extends Context.Service<
  DesktopManagedOpenSWE,
  {
    readonly start: Effect.Effect<void, DesktopManagedOpenSWEError, Scope.Scope>;
  }
>()("@t3tools/desktop/backend/DesktopManagedOpenSWE") {}

function stageError(stage: string) {
  return (cause: unknown) => new DesktopManagedOpenSWEError({ stage, cause });
}

const make = Effect.gen(function* () {
  const environment = yield* DesktopEnvironment.DesktopEnvironment;
  if (environment.platform !== "darwin") {
    return DesktopManagedOpenSWE.of({ start: Effect.void });
  }

  const fileSystem = yield* FileSystem.FileSystem;
  const net = yield* NetService.NetService;
  const crypto = yield* Crypto.Crypto;
  const spawner = yield* ChildProcessSpawner.ChildProcessSpawner;
  const httpClient = yield* HttpClient.HttpClient;

  const start = Effect.gen(function* () {
    yield* Effect.gen(function* () {
      yield* fileSystem.makeDirectory(environment.openSweDir, { recursive: true });
      yield* fileSystem.chmod(environment.openSweDir, 0o700);
      yield* fileSystem.makeDirectory(environment.openSweArtifactsDir, { recursive: true });
      yield* fileSystem.chmod(environment.openSweArtifactsDir, 0o700);
      if (!(yield* fileSystem.exists(environment.openSweProjectsFile))) {
        yield* fileSystem.writeFileString(environment.openSweProjectsFile, "[]\n", { mode: 0o600 });
      }
      yield* fileSystem.chmod(environment.openSweProjectsFile, 0o600);
    }).pipe(Effect.mapError(stageError("state setup")));

    let port: number | undefined;
    for (let candidate = START_PORT; candidate <= MAX_PORT; candidate += 1) {
      if (yield* net.canListenOnHost(candidate, "127.0.0.1")) {
        port = candidate;
        break;
      }
    }
    if (port === undefined) {
      return yield* Effect.fail(stageError("port selection")("no loopback port available"));
    }

    const token = yield* crypto
      .randomBytes(32)
      .pipe(Effect.map(Encoding.encodeHex), Effect.mapError(stageError("token generation")));
    const serverUrl = `http://127.0.0.1:${port}`;
    const child = yield* spawner
      .spawn(
        makeManagedOpenSWECommand({
          pythonPath: environment.openSwePythonPath,
          configPath: environment.openSweConfigPath,
          cwd: environment.openSweRuntimeCwd,
          port,
          token,
          projectsFile: environment.openSweProjectsFile,
          artifactsDir: environment.openSweArtifactsDir,
        }),
      )
      .pipe(Effect.mapError(stageError("process spawn")));
    yield* Effect.addFinalizer(() => child.kill().pipe(Effect.ignore));

    const request = HttpClientRequest.post(`${serverUrl}/assistants/search`).pipe(
      HttpClientRequest.bearerToken(token),
      HttpClientRequest.bodyJsonUnsafe({ graph_id: "agent", limit: 1 }),
    );
    yield* httpClient.execute(request).pipe(
      Effect.flatMap((response) =>
        response.status >= 200 && response.status < 300
          ? response.json
          : Effect.fail(new Error(`readiness returned HTTP ${response.status}`)),
      ),
      Effect.filterOrFail(
        (assistants) => Array.isArray(assistants) && assistants.length > 0,
        () => new Error("readiness did not return the agent assistant"),
      ),
      Effect.retry(Schedule.spaced(READINESS_INTERVAL)),
      Effect.timeout(READINESS_TIMEOUT),
      Effect.mapError(stageError("authenticated readiness")),
    );

    const previousEnv = Object.fromEntries(
      MANAGED_ENV_KEYS.map((key) => [key, process.env[key]]),
    ) as Record<ManagedEnvKey, string | undefined>;
    process.env.OPEN_SWE_MANAGED_SERVER_URL = serverUrl;
    process.env.OPEN_SWE_LOCAL_AUTH_TOKEN = token;
    process.env.OPEN_SWE_LOCAL_PROJECTS_FILE = environment.openSweProjectsFile;
    yield* Effect.addFinalizer(() =>
      Effect.sync(() => {
        for (const key of MANAGED_ENV_KEYS) {
          const previous = previousEnv[key];
          if (previous === undefined) delete process.env[key];
          else process.env[key] = previous;
        }
      }),
    );
  });

  return DesktopManagedOpenSWE.of({ start });
});

export const layer = Layer.effect(DesktopManagedOpenSWE, make);
