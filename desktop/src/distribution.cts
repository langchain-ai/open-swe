import path = require("node:path");
import {
  resolveAppRuntime,
  resolveBackendUrl,
  desktopLoginUrl,
} from "./config.cjs";

export type AppVariant = "open-swe" | "agent-workspace";

export class DesktopDistribution {
  constructor(readonly variant: AppVariant) {}

  get isAgentWorkspace(): boolean {
    return this.variant === "agent-workspace";
  }

  runtime(options: {
    argv: string[];
    isPackaged: boolean;
    appDataPath: string;
  }): {
    isDevelopment: boolean;
    name: string;
    appUserModelId: string;
    userDataPath: string | null;
  } {
    const runtime = resolveAppRuntime(options);
    if (!this.isAgentWorkspace) return runtime;
    const name = runtime.isDevelopment
      ? "Agent Workspace Development"
      : "Agent Workspace";
    return {
      ...runtime,
      name,
      appUserModelId: runtime.isDevelopment
        ? "com.langchain.agentworkspace.dev"
        : "com.langchain.agentworkspace",
      userDataPath: path.join(options.appDataPath, name),
    };
  }

  backendUrl(options: {
    argv: string[];
    env: NodeJS.ProcessEnv;
    isPackaged: boolean;
    storedUrl?: string;
  }): string | null {
    return this.isAgentWorkspace
      ? "https://openswe.langchain.dev/"
      : resolveBackendUrl({ ...options, storedUrl: options.storedUrl });
  }

  loginUrl(
    backendUrl: string,
    flow: { challenge: string; port: number },
  ): string {
    const target = new URL(desktopLoginUrl(backendUrl, flow));
    if (this.isAgentWorkspace) {
      target.pathname = "/dashboard/api/auth/langsmith/login";
    }
    return target.toString();
  }

  usesSharedConfig(isDevelopment: boolean): boolean {
    return !isDevelopment && !this.isAgentWorkspace;
  }
}
