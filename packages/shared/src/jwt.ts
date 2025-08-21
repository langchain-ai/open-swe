import jsonwebtoken from "jsonwebtoken";

/**
 * Generates a JWT for GitHub App authentication
 */
export function generateJWT(appId: string, privateKey: string): string {
  const now = Math.floor(Date.now() / 1000);
  const issuedAtTime = now - 60;
  const expirationTime = issuedAtTime + 10 * 60;

  const payload = {
    iat: issuedAtTime,
    exp: expirationTime,
    iss: appId,
  };

  return jsonwebtoken.sign(payload, privateKey, { algorithm: "RS256" });
}
