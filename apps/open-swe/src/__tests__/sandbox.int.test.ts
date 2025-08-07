import { test } from "@jest/globals";
import { getSandboxSessionOrThrow } from "../tools/utils/get-sandbox-id.js";
import { wrapScript } from "../utils/wrap-script.js";

test("sandbox", async () => {
  const command =
    "rg --color=never --line-number --heading -i --fixed-strings --glob '**/*.{ts,tsx,js,jsx,md,mdx,json}' --max-count 200 'configurable'";
  const sandbox = await getSandboxSessionOrThrow({
    xSandboxSessionId: "6d8def89-2db8-4d73-98b7-b8829c99ae21",
  });

  const response = await sandbox.process.executeCommand(
    command,
    "home/daytona/open-swe-dev",
    undefined,
    10000,
  );
  console.log("response");
  console.dir(response, { depth: null });

  const wrappedResponse = await sandbox.process.executeCommand(
    wrapScript(command),
    "home/daytona/open-swe-dev",
    undefined,
    10000,
  );
  console.log("wrappedResponse");
  console.dir(wrappedResponse, { depth: null });
});
