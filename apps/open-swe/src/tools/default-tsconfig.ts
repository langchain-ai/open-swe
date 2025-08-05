import path from "path";
import { tool } from "@langchain/core/tools";
import { createWriteDefaultTsConfigToolFields } from "@open-swe/shared/open-swe/tools";
import { GraphConfig, GraphState } from "@open-swe/shared/open-swe/types";
import { createShellExecutor } from "../utils/shell-executor/index.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";

const DEFAULT_TS_CONFIG = {
  extends: "@tsconfig/recommended",
  compilerOptions: {
    target: "ES2021",
    module: "NodeNext",
    lib: ["ES2023"],
    moduleResolution: "nodenext",
    esModuleInterop: true,
    noImplicitReturns: true,
    declaration: true,
    noFallthroughCasesInSwitch: true,
    noUnusedLocals: true,
    noUnusedParameters: true,
    useDefineForClassFields: true,
    strictPropertyInitialization: false,
    allowJs: true,
    strict: true,
    strictFunctionTypes: false,
    outDir: "dist",
    types: ["node"],
    resolveJsonModule: true,
  },
  include: ["**/*.ts"],
  exclude: ["node_modules", "dist"],
};

export function createWriteDefaultTsConfigTool(
  state: Pick<GraphState, "sandboxSessionId" | "targetRepository">,
  config: GraphConfig,
) {
  const writeDefaultTsConfigTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      const { workdir } = input;
      const executor = createShellExecutor(config);
      const tsConfigFileName = "tsconfig.json";

      try {
        const response = await executor.executeCommand({
          command: `echo '${JSON.stringify(DEFAULT_TS_CONFIG)}' > ${tsConfigFileName}`,
          workdir,
          timeout: TIMEOUT_SEC,
        });
        if (response.exitCode !== 0) {
          throw new Error(
            `Failed to write default tsconfig.json. Exit code: ${response.exitCode}\nError: ${response.result}`,
          );
        }

        const destinationPath = path.join(workdir, tsConfigFileName);
        return {
          result: `Successfully wrote to tsconfig.json to ${destinationPath}`,
          status: "success",
        };
      } catch (error) {
        const errorFields = getSandboxErrorFields(error);
        if (errorFields) {
          return {
            result: `Error: ${errorFields.result ?? errorFields.artifacts?.stdout}`,
            status: "error",
          };
        }

        const errorString =
          error instanceof Error ? error.message : String(error);
        return {
          result: `Error: ${errorString}`,
          status: "error",
        };
      }
    },
    createWriteDefaultTsConfigToolFields(state.targetRepository),
  );

  return writeDefaultTsConfigTool;
}
