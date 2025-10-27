import jsonwebtoken from "jsonwebtoken";

/**
 * Generates a JWT for GitHub App authentication
 * NVIDIA CUSTOMIZATION: Reduced expiration to 5 minutes to avoid GitHub rejection
 * GitHub sometimes rejects tokens with 10 minute expiration as "too far in the future"
 */
export function generateJWT(appId: string, privateKey: string): string {
  const now = Math.floor(Date.now() / 1000);
  
  // Subtract 30 seconds to account for clock skew
  const issuedAt = now - 30;

  const payload = {
    iat: issuedAt,
    exp: issuedAt + 5 * 60, // 5 minutes instead of 10 (GitHub requirement)
    iss: appId,
  };

  return jsonwebtoken.sign(payload, privateKey, { algorithm: "RS256" });
}
