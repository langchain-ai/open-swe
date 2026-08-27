const { exec } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const WANTED_POLL_INTERVAL_MS = 1_000;
const RECONNECT_DELAY_MS = 2_000;
const BASE_SOCKETS = 3;
const MAX_SOCKETS = 24;
const MAX_OUTPUT_BYTES = 4_000_000;
const MAX_UPLOAD_BYTES = 40_000_000;
const DEFAULT_TIMEOUT_S = 30 * 60;
const SUBPROTOCOL = "open-swe-runner";

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Resolve the directory a frame may run in.
 *
 * This is the trust boundary. The server relays whatever project path the
 * thread recorded, but only this process can see which directories the user
 * actually approved, so every frame is re-checked against that list rather
 * than trusted because it arrived over an authenticated socket.
 *
 * Requiring a known thread also orders things correctly: the app records a git
 * checkpoint when it registers a local thread, so refusing unregistered ones
 * means the agent can never reach the working tree before that snapshot exists.
 */
function resolveWorkingDirectory(
  frame,
  { registeredProject, deviceId, knowsThread },
) {
  if (frame.device_id !== deviceId) return null;
  if (typeof frame.thread_id !== "string" || !knowsThread(frame.thread_id))
    return null;
  if (typeof frame.project_path !== "string" || !frame.project_path) return null;
  const project = registeredProject(frame.project_path);
  if (!project) return null;
  try {
    return fs.statSync(project).isDirectory() ? project : null;
  } catch {
    return null;
  }
}

function truncate(text) {
  const buffer = Buffer.from(text, "utf8");
  if (buffer.byteLength <= MAX_OUTPUT_BYTES) return { text, truncated: false };
  return {
    text: `${buffer.subarray(0, MAX_OUTPUT_BYTES).toString("utf8")}\n[output truncated]`,
    truncated: true,
  };
}

function runCommand(command, cwd, timeoutSeconds) {
  return new Promise((resolve) => {
    const seconds =
      Number.isFinite(timeoutSeconds) && timeoutSeconds > 0
        ? timeoutSeconds
        : DEFAULT_TIMEOUT_S;
    exec(
      command,
      {
        cwd,
        timeout: seconds * 1_000,
        killSignal: "SIGKILL",
        maxBuffer: MAX_OUTPUT_BYTES,
        windowsHide: true,
      },
      (error, stdout, stderr) => {
        const combined = `${stdout ?? ""}${stderr ?? ""}`;
        const { text, truncated } = truncate(
          error && !combined ? String(error.message ?? error) : combined,
        );
        resolve({
          output: text,
          exit_code: error ? (typeof error.code === "number" ? error.code : 1) : 0,
          truncated: truncated || error?.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER",
        });
      },
    );
  });
}

function insideProject(project, candidate) {
  const relative = path.relative(project, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function resolveInsideProject(project, target) {
  if (typeof target !== "string" || !target) return null;
  const candidate = path.resolve(project, target);
  return insideProject(project, candidate) ? candidate : null;
}

async function uploadFiles(frame, project) {
  const files = Array.isArray(frame.files) ? frame.files : [];
  return files.map((file) => {
    const target = resolveInsideProject(project, isRecord(file) ? file.path : null);
    if (!target) return { error: "invalid_path" };
    let content;
    try {
      content = Buffer.from(String(file.content ?? ""), "base64");
    } catch {
      return { error: "invalid_path" };
    }
    if (content.byteLength > MAX_UPLOAD_BYTES) return { error: "permission_denied" };
    try {
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, content);
      return {};
    } catch {
      return { error: "permission_denied" };
    }
  });
}

async function downloadFiles(frame, project) {
  const paths = Array.isArray(frame.paths) ? frame.paths : [];
  return paths.map((requested) => {
    const target = resolveInsideProject(project, requested);
    if (!target) return { error: "invalid_path" };
    try {
      if (fs.statSync(target).isDirectory()) return { error: "is_directory" };
      const content = fs.readFileSync(target);
      if (content.byteLength > MAX_UPLOAD_BYTES) return { error: "permission_denied" };
      return { content: content.toString("base64") };
    } catch (error) {
      return { error: error?.code === "EACCES" ? "permission_denied" : "file_not_found" };
    }
  });
}

/**
 * Keeps this machine reachable from whichever replica is running a local
 * thread. Sockets are opened outbound because a workstation has no address the
 * cloud can dial; the extra ones exist because a request may arrive at any
 * replica behind the load balancer, and only a socket terminating on *that*
 * replica can serve it.
 */
class LocalRunner {
  constructor(options) {
    this.options = options;
    this.sockets = new Set();
    this.stopped = true;
    this.pollTimer = null;
    this.openingCount = 0;
  }

  start() {
    if (!this.stopped) return;
    this.stopped = false;
    this.ensureSockets(BASE_SOCKETS);
    this.pollTimer = setInterval(() => void this.pollWanted(), WANTED_POLL_INTERVAL_MS);
    this.pollTimer.unref?.();
  }

  stop() {
    this.stopped = true;
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = null;
    const sockets = [...this.sockets];
    this.sockets.clear();
    for (const socket of sockets) {
      try {
        socket.close();
      } catch {
        /* already gone */
      }
    }
  }

  ensureSockets(target) {
    const wanted = Math.min(target, MAX_SOCKETS);
    while (!this.stopped && this.sockets.size + this.openingCount < wanted) {
      void this.openSocket();
    }
  }

  async pollWanted() {
    if (this.stopped) return;
    try {
      const response = await this.options.request("/dashboard/api/desktop/runner/wanted");
      if (!response.ok) return;
      const body = await response.json();
      const devices = Array.isArray(body?.devices) ? body.devices : [];
      // Some replica is holding a run open waiting for us. Adding a socket is
      // the only way to be introduced to it: each attempt lands on an
      // arbitrary replica, so we keep adding until the wait clears.
      if (devices.includes(this.options.deviceId)) {
        this.ensureSockets(this.sockets.size + 2);
      }
    } catch {
      /* transient: the next tick tries again */
    }
  }

  async openSocket() {
    this.openingCount += 1;
    try {
      const response = await this.options.request("/dashboard/api/desktop/runner/connect", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ device_id: this.options.deviceId }),
      });
      if (!response.ok) throw new Error(`connect failed (${response.status})`);
      const { url, ticket } = await response.json();
      await this.attach(url, ticket);
    } catch {
      if (!this.stopped && this.sockets.size < BASE_SOCKETS) {
        setTimeout(() => this.ensureSockets(BASE_SOCKETS), RECONNECT_DELAY_MS).unref?.();
      }
    } finally {
      this.openingCount -= 1;
    }
  }

  attach(url, ticket) {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(url, [SUBPROTOCOL, ticket]);
      let settled = false;
      socket.addEventListener("open", () => {
        settled = true;
        this.sockets.add(socket);
        resolve();
      });
      socket.addEventListener("message", (event) => {
        void this.handle(socket, event.data);
      });
      socket.addEventListener("error", () => {
        if (!settled) {
          settled = true;
          reject(new Error("socket error"));
        }
      });
      socket.addEventListener("close", () => {
        this.sockets.delete(socket);
        if (!settled) {
          settled = true;
          reject(new Error("socket closed"));
          return;
        }
        if (!this.stopped) this.ensureSockets(BASE_SOCKETS);
      });
    });
  }

  send(socket, payload) {
    try {
      socket.send(JSON.stringify(payload));
    } catch {
      /* the close handler reopens */
    }
  }

  async handle(socket, raw) {
    let frame;
    try {
      frame = JSON.parse(String(raw));
    } catch {
      return;
    }
    if (!isRecord(frame) || typeof frame.id !== "string") return;
    const project = resolveWorkingDirectory(frame, this.options);
    if (!project) {
      this.send(socket, {
        id: frame.id,
        type: "error",
        message: "This thread's project is not open on this computer",
      });
      return;
    }
    try {
      if (frame.type === "exec") {
        const result = await runCommand(String(frame.command ?? ""), project, frame.timeout);
        this.send(socket, { id: frame.id, type: "exec_result", ...result });
      } else if (frame.type === "upload") {
        this.send(socket, {
          id: frame.id,
          type: "upload_result",
          results: await uploadFiles(frame, project),
        });
      } else if (frame.type === "download") {
        this.send(socket, {
          id: frame.id,
          type: "download_result",
          results: await downloadFiles(frame, project),
        });
      } else {
        this.send(socket, { id: frame.id, type: "error", message: "Unsupported request" });
      }
    } catch (error) {
      this.send(socket, {
        id: frame.id,
        type: "error",
        message: String(error?.message ?? error).slice(0, 512),
      });
    }
  }
}

module.exports = {
  LocalRunner,
  downloadFiles,
  resolveWorkingDirectory,
  runCommand,
  uploadFiles,
};
