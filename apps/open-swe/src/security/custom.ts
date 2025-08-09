import { STUDIO_USER_ID } from "./utils.js";
import { LANGGRAPH_USER_PERMISSIONS } from "../constants.js";
import * as crypto from "node:crypto";

function sha256(value: string): Buffer {
  return crypto.createHash("sha256").update(value, "utf8").digest();
}

function getConfiguredApiTokens(): string[] {
  const single = process.env.API_BEARER_TOKEN || "";
  const many = process.env.API_BEARER_TOKENS || ""; // comma-separated
  const tokens: string[] = [];
  if (single.trim()) tokens.push(single.trim());
  if (many.trim()) {
    for (const t of many.split(",")) {
      const v = t.trim();
      if (v) tokens.push(v);
    }
  }
  return tokens;
}

// Pre-hash configured tokens for constant length comparisons
let cachedAllowedTokenHashes: Buffer[] | null = null;
function getAllowedTokenHashes(): Buffer[] {
  if (cachedAllowedTokenHashes) return cachedAllowedTokenHashes;
  const tokens = getConfiguredApiTokens();
  cachedAllowedTokenHashes = tokens.map((t) => sha256(t));
  return cachedAllowedTokenHashes;
}

function timingSafeEqualBuffer(a: Buffer, b: Buffer): boolean {
  // Both buffers are same length (32 bytes) since they are SHA-256 hashes
  return crypto.timingSafeEqual(a, b);
}

export function validateApiBearerToken(token: string) {
  const allowed = getAllowedTokenHashes();
  if (allowed.length === 0) {
    // Not configured; treat as invalid
    return null;
  }
  const candidateHash = sha256(token);
  const isValid = allowed.some((h) => timingSafeEqualBuffer(candidateHash, h));
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
