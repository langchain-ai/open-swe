/**
 * @returns "agentmojo" or "agentmojo-dev" based on the NODE_ENV.
 */
export function getOpenSWELabel(): "agentmojo" | "agentmojo-dev" {
  return process.env.NODE_ENV === "production" ? "agentmojo" : "agentmojo-dev";
}

/**
 * @returns "agentmojo-auto" or "agentmojo-auto-dev" based on the NODE_ENV.
 */
export function getOpenSWEAutoAcceptLabel():
  | "agentmojo-auto"
  | "agentmojo-auto-dev" {
  return process.env.NODE_ENV === "production"
    ? "agentmojo-auto"
    : "agentmojo-auto-dev";
}

/**
 * @returns "agentmojo-max" or "agentmojo-max-dev" based on the NODE_ENV.
 */
export function getOpenSWEMaxLabel(): "agentmojo-max" | "agentmojo-max-dev" {
  return process.env.NODE_ENV === "production"
    ? "agentmojo-max"
    : "agentmojo-max-dev";
}

/**
 * @returns "agentmojo-max-auto" or "agentmojo-max-auto-dev" based on the NODE_ENV.
 */
export function getOpenSWEMaxAutoAcceptLabel():
  | "agentmojo-max-auto"
  | "agentmojo-max-auto-dev" {
  return process.env.NODE_ENV === "production"
    ? "agentmojo-max-auto"
    : "agentmojo-max-auto-dev";
}
