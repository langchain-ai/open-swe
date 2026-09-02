const { notarize } = require("@electron/notarize");

exports.default = async function (context) {
  if (context.electronPlatformName !== "darwin") return;
  if (!process.env.APPLE_API_KEY) return;

  const appName = context.packager.appInfo.productFilename;
  const appPath = `${context.appOutDir}/${appName}.app`;

  return notarize({
    appPath,
    appleApiKey: process.env.APPLE_API_KEY,
    appleApiKeyId: process.env.APPLE_API_KEY_ID,
    appleApiIssuer: process.env.APPLE_API_ISSUER,
  });
};
