import path from "node:path"

export interface LocalServerOptions {
  host: string
  port: number
  stateDirectory: string
  graphEntrypoint?: string
}

function optionValue(
  arguments_: readonly string[],
  name: string
): string | undefined {
  const index = arguments_.indexOf(name)
  return index < 0 ? undefined : arguments_[index + 1]
}

export function parseLocalServerOptions(
  arguments_: readonly string[],
  currentDirectory = process.cwd()
): LocalServerOptions {
  const port = Number.parseInt(optionValue(arguments_, "--port") ?? "2024", 10)
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error("--port must be an integer between 1 and 65535")
  }

  return {
    host: optionValue(arguments_, "--host") ?? "127.0.0.1",
    port,
    stateDirectory: path.resolve(
      currentDirectory,
      optionValue(arguments_, "--state-dir") ?? currentDirectory
    ),
    ...(optionValue(arguments_, "--graph-entrypoint")
      ? {
          graphEntrypoint: path.resolve(
            currentDirectory,
            optionValue(arguments_, "--graph-entrypoint")!
          ),
        }
      : {}),
  }
}
