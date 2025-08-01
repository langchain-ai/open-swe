import { Sandbox } from "@daytonaio/sdk";

export interface LocalExecutionArtifacts {
  stdout?: string;
  stderr?: string;
}

export interface LocalExecuteResponse {
  exitCode: number;
  result: string;
  artifacts?: LocalExecutionArtifacts;
}

export interface ExecuteCommandOptions {
  command: string | string[];
  workdir?: string;
  env?: Record<string, string>;
  timeout?: number;
  sandbox?: Sandbox;
}
