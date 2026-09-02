const path = require("node:path");

const APP_ORIGIN = "open-swe://app";
const APP_URL = `${APP_ORIGIN}/`;
const APP_NAME = "Open SWE";
const DEVELOPMENT_APP_NAME = "Open SWE Development";
const APP_USER_MODEL_ID = "com.langchain.openswe";
const DEVELOPMENT_APP_USER_MODEL_ID = "com.langchain.openswe.dev";
const DEVELOPMENT_USER_DATA_DIRECTORY = "Open SWE Development";
const DEFAULT_DEVELOPMENT_BACKEND_URL = "http://localhost:2024";
const ALLOWED_PERMISSIONS = new Set([
  "clipboard-sanitized-write",
  "notifications",
]);
const SESSION_COOKIE_NAME = "osw_session";
const LOGIN_PATH = "/dashboard/api/auth/login";
const DESKTOP_EXCHANGE_PATH = "/dashboard/api/auth/desktop/exchange";

function resolveAppRuntime({ argv, isPackaged, appDataPath }) {
  const isDevelopment = !isPackaged || argv.includes("--dev");
  return {
    isDevelopment,
    name: isDevelopment ? DEVELOPMENT_APP_NAME : APP_NAME,
    appUserModelId: isDevelopment
      ? DEVELOPMENT_APP_USER_MODEL_ID
      : APP_USER_MODEL_ID,
    userDataPath: isDevelopment
      ? path.join(appDataPath, DEVELOPMENT_USER_DATA_DIRECTORY)
      : null,
  };
}

function cliBackendUrl(argv) {
  for (const name of ["--backend-url", "--url"]) {
    const inline = argv.find((argument) => argument.startsWith(`${name}=`));
    if (inline) return inline.slice(name.length + 1);

    const index = argv.indexOf(name);
    if (index !== -1) return argv[index + 1];
  }
  return undefined;
}

function validateBackendUrl(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("Backend URL must use http or https");
  }
  return url.toString();
}

function resolveBackendUrl({ argv, env, isPackaged, storedUrl }) {
  const value =
    cliBackendUrl(argv) ||
    env.OPEN_SWE_BACKEND_URL ||
    env.OPEN_SWE_DESKTOP_URL ||
    storedUrl ||
    (isPackaged ? undefined : DEFAULT_DEVELOPMENT_BACKEND_URL);
  return value ? validateBackendUrl(value.trim()) : null;
}

function isAppUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "open-swe:" && url.hostname === "app";
  } catch {
    return false;
  }
}

function isAppLoginUrl(value) {
  try {
    return isAppUrl(value) && new URL(value).pathname === LOGIN_PATH;
  } catch {
    return false;
  }
}

function desktopLoginUrl(backendUrl, { challenge, port }) {
  const target = new URL(LOGIN_PATH, backendUrl);
  // Make OAuth redirect back through this backend's origin.
  target.searchParams.set("desktop", "true");
  target.searchParams.set("desktop_handoff", challenge);
  target.searchParams.set("desktop_port", String(port));
  return target.toString();
}

function desktopExchangeUrl(backendUrl) {
  return new URL(DESKTOP_EXCHANGE_PATH, backendUrl).toString();
}

function isTrustedPermissionRequest(
  permission,
  requestingUrl,
  details: any = {},
) {
  if (!isAppUrl(requestingUrl)) return false;
  if (ALLOWED_PERMISSIONS.has(permission)) return true;
  const mediaTypes = details.mediaTypes ?? [details.mediaType];
  return (
    permission === "media" &&
    mediaTypes.includes("audio") &&
    !mediaTypes.includes("video")
  );
}

function isTrustedProxyRequest(pageUrl) {
  return isAppUrl(pageUrl);
}

function backendRequestUrl(backendUrl, appRequestUrl) {
  if (!isAppUrl(appRequestUrl)) throw new Error("Invalid desktop request URL");
  const source = new URL(appRequestUrl);
  const target = new URL(`${source.pathname}${source.search}`, backendUrl);
  if (source.pathname === "/dashboard/api/auth/login") {
    target.searchParams.set("desktop", "true");
  }
  return target.toString();
}

function localCallbackUrl(navigationUrl, backendUrl) {
  try {
    const target = new URL(navigationUrl);
    const backend = new URL(backendUrl);
    if (
      !["http:", "https:"].includes(target.protocol) ||
      target.origin !== backend.origin ||
      !/^\/dashboard\/api\/(?:auth|slack|notion)\/callback$/.test(
        target.pathname,
      )
    ) {
      return null;
    }
    return `${APP_URL}${target.pathname.slice(1)}${target.search}${target.hash}`;
  } catch {
    return null;
  }
}

function appRedirectUrl(location) {
  const target = new URL(location, APP_URL);
  return `${APP_URL}${target.pathname.replace(/^\//, "")}${target.search}${target.hash}`;
}

function staticFilePath(root, appRequestUrl) {
  if (!isAppUrl(appRequestUrl)) return null;
  const pathname = decodeURIComponent(new URL(appRequestUrl).pathname);
  const relative = pathname.replace(/^\/+/, "");
  const rootPath = path.resolve(root);
  const candidate = path.resolve(rootPath, relative);
  if (candidate !== rootPath && !candidate.startsWith(`${rootPath}${path.sep}`))
    return null;
  return candidate;
}

module.exports = {
  APP_ORIGIN,
  APP_URL,
  DEFAULT_DEVELOPMENT_BACKEND_URL,
  SESSION_COOKIE_NAME,
  appRedirectUrl,
  desktopExchangeUrl,
  desktopLoginUrl,
  resolveAppRuntime,
  backendRequestUrl,
  isAppLoginUrl,
  isAppUrl,
  isTrustedPermissionRequest,
  isTrustedProxyRequest,
  localCallbackUrl,
  resolveBackendUrl,
  staticFilePath,
  validateBackendUrl,
};
