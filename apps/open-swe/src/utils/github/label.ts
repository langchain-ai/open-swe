/**
 * @returns "open-swe" or "open-swe-dev" based on the NODE_ENV.
 */
export function getOpenSWELabel() {
  return process.env.NODE_ENV === "production" ? "open-swe" : "open-swe-dev";
}
