import { generateJWT } from "../jwt.js";

/**
 * Gets an installation access token for a GitHub App installation
 */
export async function getInstallationToken(
  installationId: string,
  appId: string,
  privateKey: string,
): Promise<string> {
  const jwtToken = generateJWT(appId, privateKey);

  const response = await fetch(
    `https://api.github.com/app/installations/${installationId}/access_tokens`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${jwtToken}`,
        Accept: "application/vnd.github.v3+json",
        "User-Agent": "OpenSWE-Agent",
      },
    },
  );

  if (!response.ok) {
    const errorData = await response.json();
    throw new Error(
      `Failed to get installation token: ${JSON.stringify(errorData)}`,
    );
  }

  const data = await response.json();
  if (typeof data !== "object" || !data || !("token" in data)) {
    throw new Error("No token returned after fetching installation token");
  }
  return data.token as string;
}
