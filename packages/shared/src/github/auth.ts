/**
 * GitHub integration has been removed from the shared package.
 * This function now serves as a placeholder and will throw if invoked.
 */
export async function getInstallationToken(
  _installationId: string,
  _appId: string,
  _privateKey: string,
): Promise<string> {
  throw new Error("GitHub support has been removed");
}
