const { execFileSync } = require("node:child_process")
const { randomBytes } = require("node:crypto")
const { chmodSync, existsSync, realpathSync, statSync } = require("node:fs")
const path = require("node:path")
const pty = require("node-pty")

const terminals = new Map()
const VOLATILE_SHELL_VARS = ["PWD", "OLDPWD", "SHLVL"]
let shellEnv

function ensurePtySpawnHelperExecutable() {
  if (process.platform === "win32") return
  try {
    const unixTerminalPath = require.resolve("node-pty/lib/unixTerminal.js")
    const helperPath = path
      .resolve(
        path.dirname(unixTerminalPath),
        "../prebuilds",
        `${process.platform}-${process.arch}`,
        "spawn-helper"
      )
      .replace("app.asar", "app.asar.unpacked")
      .replace("node_modules.asar", "node_modules.asar.unpacked")
    const mode = statSync(helperPath).mode & 0o777
    if ((mode & 0o111) === 0) chmodSync(helperPath, mode | 0o755)
  } catch {}
}

function getUserShellEnv() {
  if (shellEnv) return shellEnv
  const shell = process.env.SHELL || "/bin/zsh"
  for (const args of [["-il", "-c"], ["-l", "-c"], ["-i", "-c"]]) {
    try {
      const mark = randomBytes(8).toString("hex")
      const result = execFileSync(shell, [...args, `echo '${mark}'; env; echo '${mark}'`], {
        encoding: "utf8",
        timeout: 10_000,
        stdio: ["pipe", "pipe", "pipe"],
      })
      const start = result.indexOf(mark)
      const end = result.lastIndexOf(mark)
      if (start === -1 || start === end) continue
      shellEnv = { ...process.env }
      for (const line of result.slice(start + mark.length, end).split("\n")) {
        const separator = line.indexOf("=")
        if (separator > 0) shellEnv[line.slice(0, separator)] = line.slice(separator + 1)
      }
      for (const key of VOLATILE_SHELL_VARS) delete shellEnv[key]
      return shellEnv
    } catch {}
  }
  shellEnv = { ...process.env }
  return shellEnv
}

function shellCandidates() {
  if (process.platform === "win32") return ["powershell.exe"]
  const candidates = [process.env.SHELL, "/bin/zsh", "/bin/bash", "/bin/sh"]
  return candidates.filter(
    (candidate, index) =>
      candidate &&
      candidates.indexOf(candidate) === index &&
      (!candidate.includes("/") || existsSync(candidate))
  )
}

function registeredCwd(cwd, listProjects) {
  if (typeof cwd !== "string" || !path.isAbsolute(cwd)) {
    throw new Error("Choose a valid local project directory")
  }
  const resolved = realpathSync(cwd)
  if (!statSync(resolved).isDirectory() || !listProjects().some((project) => project.cwd === resolved)) {
    throw new Error("Add this project to Open SWE before opening a terminal")
  }
  return resolved
}

function configureTerminalIpc({ ipcMain, requireTrusted, getWindow, listProjects }) {
  ensurePtySpawnHelperExecutable()

  ipcMain.on("desktop:terminal-create", (event, id, cwd) => {
    requireTrusted(event)
    if (typeof id !== "string" || terminals.has(id)) return
    try {
      const resolvedCwd = registeredCwd(cwd, listProjects)
      let lastError
      for (const shell of shellCandidates()) {
        try {
          const term = pty.spawn(shell, [], {
            name: "xterm-256color",
            cols: 80,
            rows: 24,
            cwd: resolvedCwd,
            env: { ...getUserShellEnv(), SHELL: shell, PWD: resolvedCwd },
          })
          terminals.set(id, { term, senderId: event.sender.id })
          term.onData((data) => {
            if (!event.sender.isDestroyed()) event.sender.send("desktop:terminal-data", id, data)
          })
          term.onExit(() => {
            if (terminals.get(id)?.term === term) terminals.delete(id)
          })
          return
        } catch (error) {
          lastError = error
        }
      }
      throw lastError || new Error("Terminal failed to start")
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      if (!event.sender.isDestroyed()) event.sender.send("desktop:terminal-error", id, message)
    }
  })

  ipcMain.on("desktop:terminal-write", (event, id, data) => {
    requireTrusted(event)
    const entry = terminals.get(id)
    if (entry?.senderId === event.sender.id && typeof data === "string") entry.term.write(data)
  })

  ipcMain.on("desktop:terminal-resize", (event, id, cols, rows) => {
    requireTrusted(event)
    const entry = terminals.get(id)
    if (
      entry?.senderId === event.sender.id &&
      Number.isFinite(cols) &&
      Number.isFinite(rows) &&
      cols > 0 &&
      rows > 0
    ) {
      entry.term.resize(Math.floor(cols), Math.floor(rows))
    }
  })

  ipcMain.on("desktop:terminal-destroy", (event, id) => {
    requireTrusted(event)
    const entry = terminals.get(id)
    if (entry?.senderId !== event.sender.id) return
    entry.term.kill()
    terminals.delete(id)
  })

  getWindow()?.webContents.once("destroyed", closeAllTerminals)
}

function closeAllTerminals() {
  for (const { term } of terminals.values()) term.kill()
  terminals.clear()
}

module.exports = { closeAllTerminals, configureTerminalIpc }
