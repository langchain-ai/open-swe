import { GraphConfig } from "@open-swe/shared/open-swe/types";
import {
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_TOKEN_COOKIE,
  GITHUB_USER_ID_HEADER,
  GITHUB_USER_LOGIN_HEADER,
  GITHUB_INSTALLATION_NAME,
  GITHUB_PAT,
  GITHUB_INSTALLATION_ID,
} from "@open-swe/shared/constants";

export function getDefaultHeaders(config: GraphConfig): Record<string, string> {
  const githubPat = config.configurable?.[GITHUB_PAT];
  const isProd = process.env.NODE_ENV === "production";
  if (githubPat && !isProd) {
    // PAT-only
    return {
      [GITHUB_PAT]: githubPat,
    };
  }

  const githubInstallationTokenCookie =
    config.configurable?.[GITHUB_INSTALLATION_TOKEN_COOKIE];
  const githubInstallationName =
    config.configurable?.[GITHUB_INSTALLATION_NAME];
  const githubInstallationId = config.configurable?.[GITHUB_INSTALLATION_ID];

  if (
    !githubInstallationTokenCookie ||
    !githubInstallationName ||
    !githubInstallationId
  ) {
    throw new Error("Missing required headers");
  }

  const githubTokenCookie = config.configurable?.[GITHUB_TOKEN_COOKIE] ?? "";
  const githubUserIdHeader = config.configurable?.[GITHUB_USER_ID_HEADER] ?? "";
  const githubUserLoginHeader =
    config.configurable?.[GITHUB_USER_LOGIN_HEADER] ?? "";

  return {
    // Required headers
    [GITHUB_INSTALLATION_TOKEN_COOKIE]: githubInstallationTokenCookie,
    [GITHUB_INSTALLATION_NAME]: githubInstallationName,
    [GITHUB_INSTALLATION_ID]: githubInstallationId,

    // Optional headers
    [GITHUB_TOKEN_COOKIE]: githubTokenCookie,
    [GITHUB_USER_ID_HEADER]: githubUserIdHeader,
    [GITHUB_USER_LOGIN_HEADER]: githubUserLoginHeader,
  };
}
