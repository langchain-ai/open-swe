/**
 * @returns "open-swe" or "open-swe-dev" based on the NODE_ENV.
 */
export function getOpenSWELabel(): "open-swe" | "open-swe-dev" {
  return process.env.NODE_ENV === "production" ? "open-swe" : "open-swe-dev";
}

/**
 * @returns "open-swe-auto" or "open-swe-auto-dev" based on the NODE_ENV.
 */
export function getOpenSWEAutoAcceptLabel():
  | "open-swe-auto"
  | "open-swe-auto-dev" {
  return process.env.NODE_ENV === "production"
    ? "open-swe-auto"
    : "open-swe-auto-dev";
}
