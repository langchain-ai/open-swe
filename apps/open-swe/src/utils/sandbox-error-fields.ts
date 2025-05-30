import { ExecuteResponse } from "@daytonaio/sdk/dist/types/ExecuteResponse.js";

export function getSandboxErrorFields(
  error: unknown,
): ExecuteResponse | undefined {
  if (
    !error ||
    typeof error !== "object" ||
    !("result" in error) ||
    !error.result ||
    typeof error.result !== "object" ||
    !("exitCode" in error.result) ||
    !("stderr" in error.result) ||
    !("stdout" in error.result)
  ) {
    return undefined;
  }

  return error.result as unknown as ExecuteResponse;
}
