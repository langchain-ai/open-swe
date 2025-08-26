/**
 * @returns "agent-mojo" or "agent-mojo-dev" based on the NODE_ENV.
 */
export function getOpenSWELabel(): "agent-mojo" | "agent-mojo-dev" {
  return process.env.NODE_ENV === "production" ? "agent-mojo" : "agent-mojo-dev";
}

/**
 * @returns "agent-mojo-auto" or "agent-mojo-auto-dev" based on the NODE_ENV.
 */
export function getOpenSWEAutoAcceptLabel():
  | "agent-mojo-auto"
  | "agent-mojo-auto-dev" {
  return process.env.NODE_ENV === "production"
    ? "agent-mojo-auto"
    : "agent-mojo-auto-dev";
}

/**
 * @returns "agent-mojo-max" or "agent-mojo-max-dev" based on the NODE_ENV.
 */
export function getOpenSWEMaxLabel(): "agent-mojo-max" | "agent-mojo-max-dev" {
  return process.env.NODE_ENV === "production"
    ? "agent-mojo-max"
    : "agent-mojo-max-dev";
}

/**
 * @returns "agent-mojo-max-auto" or "agent-mojo-max-auto-dev" based on the NODE_ENV.
 */
export function getOpenSWEMaxAutoAcceptLabel():
  | "agent-mojo-max-auto"
  | "agent-mojo-max-auto-dev" {
  return process.env.NODE_ENV === "production"
    ? "agent-mojo-max-auto"
    : "agent-mojo-max-auto-dev";
}
