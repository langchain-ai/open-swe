import { GraphConfig } from "@open-swe/shared/open-swe/types";
import {
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_TOKEN_COOKIE,
  GITHUB_USER_ID_HEADER,
  GITHUB_USER_LOGIN_HEADER,
  GITHUB_INSTALLATION_ID_COOKIE_X_PREFIX,
} from "@open-swe/shared/constants";

export function getDefaultHeaders(config: GraphConfig) {
  const githubInstallationTokenCookie =
    config.configurable?.[GITHUB_INSTALLATION_TOKEN_COOKIE];
  const githubInstallationIdCookieXPrefix =
    config.configurable?.[GITHUB_INSTALLATION_ID_COOKIE_X_PREFIX];

  if (!githubInstallationTokenCookie || !githubInstallationIdCookieXPrefix) {
    throw new Error("Missing required headers");
  }

  const githubTokenCookie = config.configurable?.[GITHUB_TOKEN_COOKIE] ?? "";
  const githubUserIdHeader = config.configurable?.[GITHUB_USER_ID_HEADER] ?? "";
  const githubUserLoginHeader =
    config.configurable?.[GITHUB_USER_LOGIN_HEADER] ?? "";

  return {
    // Required headers
    [GITHUB_INSTALLATION_TOKEN_COOKIE]: githubInstallationTokenCookie,
    [GITHUB_INSTALLATION_ID_COOKIE_X_PREFIX]: githubInstallationIdCookieXPrefix,

    // Optional headers
    [GITHUB_TOKEN_COOKIE]: githubTokenCookie,
    [GITHUB_USER_ID_HEADER]: githubUserIdHeader,
    [GITHUB_USER_LOGIN_HEADER]: githubUserLoginHeader,
  };
}
