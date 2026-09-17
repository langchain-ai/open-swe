export { createLocalBackend } from "./backend/local.ts"
export type { LocalBackendOptions } from "./backend/local.ts"
export type { ExecuteConfig } from "./backend/execute.ts"
export {
  isInside,
  PathOutsideRootError,
  resolveWithinRoot,
} from "./backend/paths.ts"
export type * from "./backend/types.ts"

export { createReplayGuard, signRequest, verifyRequest } from "./server/auth.ts"
export type {
  AuthConfig,
  ReplayGuard,
  SignatureInput,
  VerifyResult,
} from "./server/auth.ts"
export { createWorkstationServer } from "./server/server.ts"
export type {
  WorkstationLogger,
  WorkstationServer,
  WorkstationServerOptions,
} from "./server/server.ts"
