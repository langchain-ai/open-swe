// @effect-diagnostics nodeBuiltinImport:off - Tests exercise root env file precedence directly.
import * as NodeFS from "node:fs";
import * as NodeOS from "node:os";
import * as NodePath from "node:path";
import { afterEach, describe, expect, it } from "vite-plus/test";

import { loadRepoEnv, resolvePublicConfig } from "./public-config.ts";

const temporaryDirectories: string[] = [];

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    NodeFS.rmSync(directory, { recursive: true, force: true });
  }
});

describe("loadRepoEnv", () => {
  it("does not project cloud configuration for an unconfigured clone", () => {
    const env = loadRepoEnv({ baseEnv: {}, repoRoot: makeTemporaryDirectory() });

    expect(env.OPENSWE_CLERK_PUBLISHABLE_KEY).toBeUndefined();
    expect(env.OPENSWE_CLERK_CLI_OAUTH_CLIENT_ID).toBeUndefined();
    expect(env.VITE_CLERK_PUBLISHABLE_KEY).toBeUndefined();
    expect(env.EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY).toBeUndefined();
    expect(env.OPENSWE_CLERK_JWT_TEMPLATE).toBeUndefined();
    expect(env.VITE_CLERK_JWT_TEMPLATE).toBeUndefined();
    expect(env.EXPO_PUBLIC_CLERK_JWT_TEMPLATE).toBeUndefined();
    expect(env.OPENSWE_RELAY_URL).toBeUndefined();
    expect(env.VITE_OPENSWE_RELAY_URL).toBeUndefined();
    expect(env.OPENSWE_MOBILE_OTLP_TRACES_URL).toBeUndefined();
    expect(env.OPENSWE_MOBILE_OTLP_TRACES_DATASET).toBeUndefined();
    expect(env.OPENSWE_MOBILE_OTLP_TRACES_TOKEN).toBeUndefined();
    expect(env.EXPO_PUBLIC_OTLP_TRACES_URL).toBeUndefined();
    expect(env.EXPO_PUBLIC_OTLP_TRACES_DATASET).toBeUndefined();
    expect(env.EXPO_PUBLIC_OTLP_TRACES_TOKEN).toBeUndefined();
    expect(env.OPENSWE_RELAY_CLIENT_OTLP_TRACES_URL).toBeUndefined();
    expect(env.OPENSWE_RELAY_CLIENT_OTLP_TRACES_DATASET).toBeUndefined();
    expect(env.OPENSWE_RELAY_CLIENT_OTLP_TRACES_TOKEN).toBeUndefined();
    expect(env.VITE_RELAY_OTLP_TRACES_URL).toBeUndefined();
    expect(env.VITE_RELAY_OTLP_TRACES_DATASET).toBeUndefined();
    expect(env.VITE_RELAY_OTLP_TRACES_TOKEN).toBeUndefined();
  });

  it("applies process, root local, and root precedence in that order", () => {
    const repoRoot = makeTemporaryDirectory();
    NodeFS.writeFileSync(
      NodePath.join(repoRoot, ".env"),
      "OPENSWE_CLERK_PUBLISHABLE_KEY=pk_root\nOPENSWE_CLERK_JWT_TEMPLATE=template_root\nOPENSWE_CLERK_CLI_OAUTH_CLIENT_ID=oauth_root\nOPENSWE_RELAY_URL=https://root.example.test\n",
    );
    NodeFS.writeFileSync(
      NodePath.join(repoRoot, ".env.local"),
      "OPENSWE_CLERK_PUBLISHABLE_KEY=pk_local\nOPENSWE_CLERK_JWT_TEMPLATE=template_local\nOPENSWE_CLERK_CLI_OAUTH_CLIENT_ID=oauth_local\nOPENSWE_RELAY_URL=https://local.example.test\n",
    );

    expect(loadRepoEnv({ baseEnv: {}, repoRoot }).OPENSWE_RELAY_URL).toBe(
      "https://local.example.test",
    );
    expect(
      loadRepoEnv({
        baseEnv: {
          OPENSWE_CLERK_PUBLISHABLE_KEY: "pk_ci",
          OPENSWE_CLERK_JWT_TEMPLATE: "template_ci",
          OPENSWE_CLERK_CLI_OAUTH_CLIENT_ID: "oauth_ci",
          OPENSWE_RELAY_URL: "https://ci.example.test",
        },
        repoRoot,
      }),
    ).toMatchObject({
      OPENSWE_CLERK_PUBLISHABLE_KEY: "pk_ci",
      OPENSWE_CLERK_CLI_OAUTH_CLIENT_ID: "oauth_ci",
      VITE_CLERK_PUBLISHABLE_KEY: "pk_ci",
      EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_ci",
      OPENSWE_CLERK_JWT_TEMPLATE: "template_ci",
      VITE_CLERK_JWT_TEMPLATE: "template_ci",
      EXPO_PUBLIC_CLERK_JWT_TEMPLATE: "template_ci",
      OPENSWE_RELAY_URL: "https://ci.example.test",
      VITE_OPENSWE_RELAY_URL: "https://ci.example.test",
    });
  });

  it("accepts legacy framework aliases as root overrides", () => {
    expect(
      resolvePublicConfig({
        VITE_CLERK_PUBLISHABLE_KEY: "pk_legacy",
        VITE_CLERK_JWT_TEMPLATE: "template_legacy",
        OPENSWE_CLERK_CLI_OAUTH_CLIENT_ID: "oauth_canonical",
        VITE_OPENSWE_RELAY_URL: "https://legacy.example.test",
        EXPO_PUBLIC_OTLP_TRACES_URL: "https://api.axiom.co/v1/traces",
        EXPO_PUBLIC_OTLP_TRACES_DATASET: "mobile-traces",
        EXPO_PUBLIC_OTLP_TRACES_TOKEN: "mobile-token",
      }),
    ).toEqual({
      clerkPublishableKey: "pk_legacy",
      clerkJwtTemplate: "template_legacy",
      clerkCliOAuthClientId: "oauth_canonical",
      relayUrl: "https://legacy.example.test",
      mobileOtlpTracesUrl: "https://api.axiom.co/v1/traces",
      mobileOtlpTracesDataset: "mobile-traces",
      mobileOtlpTracesToken: "mobile-token",
      relayClientOtlpTracesUrl: undefined,
      relayClientOtlpTracesDataset: undefined,
      relayClientOtlpTracesToken: undefined,
    });
  });

  it("projects canonical relay client tracing values to web build aliases", () => {
    expect(
      loadRepoEnv({
        baseEnv: {
          OPENSWE_RELAY_CLIENT_OTLP_TRACES_URL: "https://api.axiom.co/v1/traces",
          OPENSWE_RELAY_CLIENT_OTLP_TRACES_DATASET: "relay-client-traces",
          OPENSWE_RELAY_CLIENT_OTLP_TRACES_TOKEN: "relay-client-token",
        },
        repoRoot: makeTemporaryDirectory(),
      }),
    ).toEqual({
      OPENSWE_RELAY_CLIENT_OTLP_TRACES_URL: "https://api.axiom.co/v1/traces",
      OPENSWE_RELAY_CLIENT_OTLP_TRACES_DATASET: "relay-client-traces",
      OPENSWE_RELAY_CLIENT_OTLP_TRACES_TOKEN: "relay-client-token",
      VITE_RELAY_OTLP_TRACES_URL: "https://api.axiom.co/v1/traces",
      VITE_RELAY_OTLP_TRACES_DATASET: "relay-client-traces",
      VITE_RELAY_OTLP_TRACES_TOKEN: "relay-client-token",
    });
  });

  it("projects canonical mobile tracing values to Expo public aliases", () => {
    expect(
      loadRepoEnv({
        baseEnv: {
          OPENSWE_RELAY_URL: "https://relay.example.test",
          OPENSWE_MOBILE_OTLP_TRACES_URL: "https://api.axiom.co/v1/traces",
          OPENSWE_MOBILE_OTLP_TRACES_DATASET: "mobile-traces",
          OPENSWE_MOBILE_OTLP_TRACES_TOKEN: "mobile-token",
        },
        repoRoot: makeTemporaryDirectory(),
      }),
    ).toEqual({
      OPENSWE_RELAY_URL: "https://relay.example.test",
      VITE_OPENSWE_RELAY_URL: "https://relay.example.test",
      OPENSWE_MOBILE_OTLP_TRACES_URL: "https://api.axiom.co/v1/traces",
      OPENSWE_MOBILE_OTLP_TRACES_DATASET: "mobile-traces",
      OPENSWE_MOBILE_OTLP_TRACES_TOKEN: "mobile-token",
      EXPO_PUBLIC_OTLP_TRACES_URL: "https://api.axiom.co/v1/traces",
      EXPO_PUBLIC_OTLP_TRACES_DATASET: "mobile-traces",
      EXPO_PUBLIC_OTLP_TRACES_TOKEN: "mobile-token",
    });
  });
});

function makeTemporaryDirectory() {
  const directory = NodeFS.mkdtempSync(NodePath.join(NodeOS.tmpdir(), "openswe-public-config-"));
  temporaryDirectories.push(directory);
  return directory;
}
