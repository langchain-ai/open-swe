import { auth, defineIdentity } from "managed-deepagents";

// Who may call this deployment.
//
// LangSmith workspace API keys authenticate callers through `x-api-key` while
// retaining MDA's thread and store authorization hooks.
//
// Managed identity gives every caller private threads and downstream
// credentials. Durable memory is not an identity axis — declare it in
// `memory.ts`.
export const identity = defineIdentity({
  auth: auth.langsmithApiKey(),
});
