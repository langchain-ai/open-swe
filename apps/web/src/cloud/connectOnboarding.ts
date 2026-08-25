import * as Schema from "effect/Schema";

/**
 * Accounts that opted out of the post-sign-in Open SWE Connect onboarding wizard
 * ("Don't show this again"). The wizard otherwise shows on every sign-in,
 * since sign-out clears the connected environments.
 */
export const CONNECT_ONBOARDING_OPT_OUT_STORAGE_KEY = "openswe:connect-onboarding-opt-out:v1";

export const ConnectOnboardingOptOutSchema = Schema.Struct({
  optOutAccounts: Schema.Array(Schema.String),
});

export type ConnectOnboardingOptOutState = typeof ConnectOnboardingOptOutSchema.Type;

export const EMPTY_CONNECT_ONBOARDING_OPT_OUT_STATE: ConnectOnboardingOptOutState = {
  optOutAccounts: [],
};
