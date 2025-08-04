import { encryptSecret } from "@open-swe/shared/crypto";
import { getInstallationToken } from "@open-swe/shared/github/auth";

export async function regenerateInstallationToken(
  installationId: string | undefined,
): Promise<string> {
  if (!installationId) {
    throw new Error(
      "Missing installation ID for regenerating installation token.",
    );
  }

  const appId = process.env.GITHUB_APP_ID;
  const privateKey = process.env.GITHUB_APP_PRIVATE_KEY;
  const secretsEncryptionKey = process.env.SECRETS_ENCRYPTION_KEY;
  if (!appId || !privateKey || !secretsEncryptionKey) {
    throw new Error(
      "Missing environment variables for regenerating installation token.",
    );
  }

  const newInstallationToken = await getInstallationToken(
    installationId,
    appId,
    privateKey,
  );
  return encryptSecret(newInstallationToken, secretsEncryptionKey);
}
