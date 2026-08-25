import { createAssetEnvironmentAtoms } from "@openswe/client-runtime/state/assets";

import { connectionAtomRuntime } from "../connection/runtime";

export const assetEnvironment = createAssetEnvironmentAtoms(connectionAtomRuntime);
