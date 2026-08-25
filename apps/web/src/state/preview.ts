import { createPreviewEnvironmentAtoms } from "@openswe/client-runtime/state/preview";

import { connectionAtomRuntime } from "../connection/runtime";

export const previewEnvironment = createPreviewEnvironmentAtoms(connectionAtomRuntime);
