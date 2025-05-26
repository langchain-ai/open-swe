import { CommandResult } from "@e2b/code-interpreter";

export function getSandboxErrorFields(
  error: unknown,
): CommandResult | undefined {
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

  return error.result as CommandResult;
}
