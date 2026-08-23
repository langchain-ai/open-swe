import path from "node:path"

export interface AppServerOptions {
  port: number
  stateDirectory: string
  uiEntrypoint: string
  graphEntrypoint?: string
  backendUrl?: string
}

function optionValue(
  arguments_: readonly string[],
  name: string
): string | undefined {
  const index = arguments_.indexOf(name)
  return index < 0 ? undefined : arguments_[index + 1]
}

/**
 * Options for the combined server — one process serving the dashboard and the
 * graph, launched either by the desktop app or on its own.
 */
export function parseAppServerOptions(
  arguments_: readonly string[],
  environment: NodeJS.ProcessEnv = process.env,
  currentDirectory = process.cwd(),
  /** Where the dashboard build sits when the caller does not say. */
  defaultUiEntrypoint?: string
): AppServerOptions {
  const rawPort =
    optionValue(arguments_, "--port") ?? environment.PORT ?? "3100"
  const port = Number.parseInt(rawPort, 10)
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error("--port must be an integer between 1 and 65535")
  }

  const uiEntrypoint =
    optionValue(arguments_, "--ui-entrypoint") ??
    environment.OPEN_SWE_UI_ENTRYPOINT ??
    defaultUiEntrypoint
  if (!uiEntrypoint) {
    throw new Error(
      "--ui-entrypoint (or OPEN_SWE_UI_ENTRYPOINT) must point at the built dashboard server"
    )
  }

  const backendUrl =
    optionValue(arguments_, "--backend-url") ?? environment.DASHBOARD_API_URL

  return {
    port,
    stateDirectory: path.resolve(
      currentDirectory,
      optionValue(arguments_, "--state-dir") ?? currentDirectory
    ),
    uiEntrypoint: path.resolve(currentDirectory, uiEntrypoint),
    ...(optionValue(arguments_, "--graph-entrypoint")
      ? {
          graphEntrypoint: path.resolve(
            currentDirectory,
            optionValue(arguments_, "--graph-entrypoint")!
          ),
        }
      : {}),
    ...(backendUrl ? { backendUrl } : {}),
  }
}
