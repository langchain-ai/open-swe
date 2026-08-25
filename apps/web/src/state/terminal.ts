import { createTerminalEnvironmentAtoms } from "@openswe/client-runtime/state/terminal";

import { connectionAtomRuntime } from "../connection/runtime";

export const terminalEnvironment = createTerminalEnvironmentAtoms(connectionAtomRuntime);
