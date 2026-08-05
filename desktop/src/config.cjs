const path = require("node:path")

const APP_URL = "open-swe://app/"
const DEFAULT_DEVELOPMENT_BACKEND_URL = "http://localhost:2024"
const ALLOWED_PERMISSIONS = new Set(["clipboard-sanitized-write", "notifications"])

function cliBackendUrl(argv) {
  for (const name of ["--backend-url", "--url"]) {
    const inline = argv.find((argument) => argument.startsWith(`${name}=`))
    if (inline) return inline.slice(name.length + 1)

    const index = argv.indexOf(name)
    if (index !== -1) return argv[index + 1]
  }
  return undefined
}

function validateBackendUrl(value) {
  const url = new URL(value)
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("Backend URL must use http or https")
  }
  return url.toString()
}

function resolveBackendUrl({ argv, env, isPackaged, storedUrl }) {
  const value =
    cliBackendUrl(argv) ||
    env.OPEN_SWE_BACKEND_URL ||
    env.OPEN_SWE_DESKTOP_URL ||
    storedUrl ||
    (isPackaged ? undefined : DEFAULT_DEVELOPMENT_BACKEND_URL)
  return value ? validateBackendUrl(value.trim()) : null
}

function isAppUrl(value) {
  try {
    const url = new URL(value)
    return url.protocol === "open-swe:" && url.hostname === "app"
  } catch {
    return false
  }
}

function isTrustedPermissionRequest(permission, requestingUrl) {
  return ALLOWED_PERMISSIONS.has(permission) && isAppUrl(requestingUrl)
}

function backendRequestUrl(backendUrl, appRequestUrl) {
  if (!isAppUrl(appRequestUrl)) throw new Error("Invalid desktop request URL")
  const source = new URL(appRequestUrl)
  const target = new URL(`${source.pathname}${source.search}`, backendUrl)
  if (source.pathname === "/dashboard/api/auth/login") {
    target.searchParams.set("desktop", "true")
  }
  return target.toString()
}

function localCallbackUrl(navigationUrl) {
  try {
    const target = new URL(navigationUrl)
    if (
      !["http:", "https:"].includes(target.protocol) ||
      !/^\/dashboard\/api\/(?:auth|slack|notion)\/callback$/.test(target.pathname)
    ) {
      return null
    }
    return `${APP_URL}${target.pathname.slice(1)}${target.search}${target.hash}`
  } catch {
    return null
  }
}

function appRedirectUrl(location) {
  const target = new URL(location, APP_URL)
  return `${APP_URL}${target.pathname.replace(/^\//, "")}${target.search}${target.hash}`
}

function staticFilePath(root, appRequestUrl) {
  if (!isAppUrl(appRequestUrl)) return null
  const pathname = decodeURIComponent(new URL(appRequestUrl).pathname)
  const relative = pathname.replace(/^\/+/, "")
  const rootPath = path.resolve(root)
  const candidate = path.resolve(rootPath, relative)
  if (candidate !== rootPath && !candidate.startsWith(`${rootPath}${path.sep}`)) return null
  return candidate
}

module.exports = {
  APP_URL,
  DEFAULT_DEVELOPMENT_BACKEND_URL,
  appRedirectUrl,
  backendRequestUrl,
  isAppUrl,
  isTrustedPermissionRequest,
  localCallbackUrl,
  resolveBackendUrl,
  staticFilePath,
  validateBackendUrl,
}
