import { GITHUB_PAT } from "@open-swe/shared/constants";
import { decryptSecret } from "@open-swe/shared/crypto";

/**
 * Simple helper to check if request has GitHub PAT and return decrypted value
 */
export function getGitHubPatFromRequest(
  request: Request,
  encryptionKey: string,
): string | null {
  const encryptedGitHubPat = request.headers.get(GITHUB_PAT);
  if (!encryptedGitHubPat) {
    return null;
  }
  return decryptSecret(encryptedGitHubPat, encryptionKey);
}

/**
 * Helper to check if configurable has GitHub PAT and return decrypted value
 */
export function getGitHubPatFromConfig(
  configurable: Record<string, any> | undefined,
  encryptionKey: string,
): string | null {
  if (!configurable) {
    return null;
  }
  const encryptedGitHubPat = configurable[GITHUB_PAT];
  if (!encryptedGitHubPat) {
    return null;
  }
  return decryptSecret(encryptedGitHubPat, encryptionKey);
}
