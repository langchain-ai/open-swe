const DEFAULT_DEVELOPMENT_URL = "http://localhost:3000"
const ALLOWED_PERMISSIONS = new Set(["clipboard-sanitized-write", "notifications"])

function cliUrl(argv) {
  const inline = argv.find((argument) => argument.startsWith("--url="))
  if (inline) return inline.slice("--url=".length)

  const index = argv.indexOf("--url")
  return index === -1 ? undefined : argv[index + 1]
}

function validateDashboardUrl(value) {
  const url = new URL(value)
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("Dashboard URL must use http or https")
  }
  return url.toString()
}

function resolveDashboardUrl({ argv, env, isPackaged, storedUrl }) {
  const value =
    cliUrl(argv) ||
    env.OPEN_SWE_DESKTOP_URL ||
    storedUrl ||
    (isPackaged ? undefined : DEFAULT_DEVELOPMENT_URL)
  return value ? validateDashboardUrl(value.trim()) : null
}

function isTrustedPermissionRequest(dashboardUrl, permission, requestingUrl) {
  if (!ALLOWED_PERMISSIONS.has(permission)) return false
  try {
    return new URL(dashboardUrl).origin === new URL(requestingUrl).origin
  } catch {
    return false
  }
}

module.exports = {
  DEFAULT_DEVELOPMENT_URL,
  isTrustedPermissionRequest,
  resolveDashboardUrl,
  validateDashboardUrl,
}
