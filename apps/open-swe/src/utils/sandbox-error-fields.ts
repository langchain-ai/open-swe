export interface ExecuteResponse {
  result?: string;
  exitCode: number;
  artifacts?: { stdout?: string; stderr?: string };
  [key: string]: unknown;
}

export function getSandboxErrorFields(
  error: unknown,
): ExecuteResponse | undefined {
  if (
    !error ||
    typeof error !== "object" ||
    !("result" in error) ||
    !error.result ||
    typeof error.result !== "string" ||
    !("exitCode" in error) ||
    typeof error.exitCode !== "number"
  ) {
    return undefined;
  }

  return error as ExecuteResponse;
}
