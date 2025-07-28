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

/**
 * @returns "open-swe-max" or "open-swe-max-dev" based on the NODE_ENV.
 */
export function getOpenSWEMaxLabel(): "open-swe-max" | "open-swe-max-dev" {
  return process.env.NODE_ENV === "production"
    ? "open-swe-max"
    : "open-swe-max-dev";
}

/**
 * @returns "open-swe-max-auto" or "open-swe-max-auto-dev" based on the NODE_ENV.
 */
export function getOpenSWEMaxAutoAcceptLabel():
  | "open-swe-max-auto"
  | "open-swe-max-auto-dev" {
  return process.env.NODE_ENV === "production"
    ? "open-swe-max-auto"
    : "open-swe-max-auto-dev";
}
