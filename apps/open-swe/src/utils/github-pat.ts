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
 * Simple helper to check if config has GitHub PAT and return decrypted value
 */
export function getGitHubPatFromConfig(
  config: Record<string, any>,
  encryptionKey: string,
): string | null {
  const encryptedGitHubPat = config[GITHUB_PAT];
  if (!encryptedGitHubPat) {
    return null;
  }
  return decryptSecret(encryptedGitHubPat, encryptionKey);
}
