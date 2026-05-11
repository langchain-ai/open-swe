export const SEARCH_RESULTS_LIMIT = 10;
export const AGENT_RECURSION_LIMIT = 9999;

// Highest cli_api_version this CLI understands. The server publishes its own
// cli_api_version on GET /cli/config; we refuse to operate against a server
// whose version is higher than this (forward-incompatible) and warn on lower
// (likely backward-compatible).
export const CLIENT_CLI_API_VERSION = 1;