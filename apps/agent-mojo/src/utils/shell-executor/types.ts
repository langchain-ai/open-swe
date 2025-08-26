import { Sandbox } from "@daytonaio/sdk";

export interface LocalExecuteResponse {
  exitCode: number;
  result: string;
  artifacts?: {
    stdout: string;
    stderr?: string;
  };
}

export interface ExecuteCommandOptions {
  command: string | string[];
  workdir?: string;
  env?: Record<string, string>;
  timeout?: number;
  sandbox?: Sandbox;
  sandboxSessionId?: string;
}
