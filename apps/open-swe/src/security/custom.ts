import { STUDIO_USER_ID } from "./utils.js";
import { LANGGRAPH_USER_PERMISSIONS } from "../constants.js";
import * as bcrypt from "bcrypt";

function bcryptHash(value: string): string {
  // Use 12 salt rounds for reasonable security
  return bcrypt.hashSync(value, 12);
}

function getConfiguredApiTokens(): string[] {
  const single = process.env.API_BEARER_TOKEN || "";
  const many = process.env.API_BEARER_TOKENS || ""; // comma-separated

  const tokens: string[] = [];

  if (single.trim()) {
    tokens.push(single.trim());
  }

  if (many.trim()) {
    for (const t of many.split(",")) {
      const v = t.trim();
      if (v) tokens.push(v);
    }
  }

  return tokens;
}

// Pre-hash configured tokens for constant length comparisons
let cachedAllowedTokenHashes: string[] | null = null;
function getAllowedTokenHashes(): string[] {
  if (cachedAllowedTokenHashes) {
    return cachedAllowedTokenHashes;
  }

  const tokens = getConfiguredApiTokens();
  cachedAllowedTokenHashes = tokens.map((t) => bcryptHash(t));
  return cachedAllowedTokenHashes;
}

export function validateApiBearerToken(token: string) {
  const allowed = getAllowedTokenHashes();
  if (allowed.length === 0) {
    // Not configured; treat as invalid
    return null;
  }

  // Compare the token against each allowed hash using bcrypt
  const isValid = allowed.some((h) => bcrypt.compareSync(token, h));
  if (isValid) {
    return {
      identity: STUDIO_USER_ID,
      is_authenticated: true,
      display_name: STUDIO_USER_ID,
      metadata: {
        installation_name: "api-key-auth",
      },
      permissions: LANGGRAPH_USER_PERMISSIONS,
    };
  }
  return null;
}
