const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  createTerminalManager,
  ensurePtySpawnHelperExecutable,
  getProjectShellEnv,
} = require("../build/terminal-manager.cjs");

class FakeProcess {
  constructor(pid, initialData) {
    this.pid = pid;
    this.initialData = initialData;
    this.writes = [];
    this.resizes = [];
    this.kills = [];
  }

  onData(listener) {
    this.dataListener = listener;
    if (this.initialData) listener(this.initialData);
    return { dispose: () => (this.dataListener = null) };
  }

  onExit(listener) {
    this.exitListener = listener;
    return { dispose: () => (this.exitListener = null) };
  }

  write(data) {
    this.writes.push(data);
  }

  resize(cols, rows) {
    this.resizes.push({ cols, rows });
  }

  kill(signal) {
    this.kills.push(signal);
  }

  data(value) {
    this.dataListener?.(value);
  }

  exit(value = { exitCode: 0, signal: 0 }) {
    this.exitListener?.(value);
  }
}

function fixture(options = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-terminal-"));
  const child = path.join(root, "child");
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-outside-"));
  fs.mkdirSync(child);
  const processes = [];
  const spawnInputs = [];
  const adapter = {
    spawn(shell, args, input) {
      spawnInputs.push({ shell, args, input });
      const process = new FakeProcess(
        100 + processes.length,
        options.initialData,
      );
      processes.push(process);
      return process;
    },
  };
  const localThreads = new Map([["acp-1", { worktreePath: root }]]);
  const manager = createTerminalManager({
    logsDir: path.join(root, ".history"),
    getLocalThread: (id) => localThreads.get(id),
    pty: adapter,
    env: {
      PATH: "/usr/bin",
      OPEN_SWE_SECRET: "blocked",
      ELECTRON_RUN_AS_NODE: "1",
    },
    inspect: async () => null,
    killGraceMs: 1,
    historyLines: 3,
    ...options,
  });
  return {
    root,
    child,
    outside,
    processes,
    spawnInputs,
    localThreads,
    manager,
  };
}

function request(cwd, extra = {}) {
  return { localSessionId: "acp-1", terminalId: "term-1", cwd, ...extra };
}

function tick() {
  return new Promise((resolve) => setImmediate(resolve));
}

test("loads environment set by interactive shell prompt hooks", async () => {
  const env = await getProjectShellEnv({
    cwd: "/project",
    env: { SHELL: "/bin/zsh", EXISTING: "kept" },
    run: async (_shell, args, options, input) => {
      assert.deepEqual(args, ["-il"]);
      assert.equal(options.cwd, "/project");
      const mark = /echo '([0-9a-f]+)'/.exec(input)[1];
      return `${mark}\nOPENAI_BASE_URL=https://gateway.example/openai/v1\n${mark}\n`;
    },
  });

  assert.equal(env.EXISTING, "kept");
  assert.equal(env.OPENAI_BASE_URL, "https://gateway.example/openai/v1");
});

test("keeps terminal identity scoped to the local thread and validates launch boundaries", async (t) => {
  const value = fixture();
  t.after(() => value.manager.shutdown());

  const first = await value.manager.open(
    request(value.child, { env: { SAFE_VALUE: "yes" } }),
  );
  const second = await value.manager.open(request(value.child));

  assert.equal(first.status, "running");
  assert.equal(second.pid, first.pid);
  assert.equal(value.processes.length, 1);
  assert.equal(value.spawnInputs[0].input.env.SAFE_VALUE, "yes");
  assert.equal(value.spawnInputs[0].input.env.OPEN_SWE_SECRET, undefined);
  assert.equal(value.spawnInputs[0].input.env.ELECTRON_RUN_AS_NODE, undefined);
  await assert.rejects(
    value.manager.open(request(value.outside)),
    /local session's worktree/,
  );
  await assert.rejects(
    value.manager.open(request(value.child, { env: { OPEN_SWE_TOKEN: "no" } })),
    /environment/,
  );
  await assert.rejects(
    value.manager.open({
      ...request(value.child),
      terminalId: "../escape".repeat(20),
    }),
    /terminal ID/,
  );
});

test("attach snapshots include raced output once and continue with ordered events", async (t) => {
  const value = fixture({ initialData: "raced output\n" });
  t.after(() => value.manager.shutdown());
  const events = [];

  const attached = await value.manager.attach(request(value.root), (event) =>
    events.push(event),
  );
  value.processes[0].data("live output\n");
  value.processes[0].exit({ exitCode: 7, signal: 0 });
  await tick();

  assert.match(attached.snapshot.history, /raced output/);
  assert.deepEqual(
    events.map((event) => event.type),
    ["output", "exited"],
  );
  assert.ok(events[0].sequence > attached.snapshot.sequence);
  assert.ok(events[1].sequence > events[0].sequence);
  attached.detach();
});

test("persists bounded sanitized history and supports clear, restart, detach, and close", async (t) => {
  const value = fixture();
  t.after(() => value.manager.shutdown());
  const attached = await value.manager.attach(request(value.root), () => {});
  const process = value.processes[0];

  process.data("one\ntwo\n\u001b[6nthree\nfour\n");
  await value.manager.clear(request(value.root));
  assert.equal(
    (await value.manager.attach(request(value.root), () => {})).snapshot
      .history,
    "",
  );

  process.data("one\ntwo\n\u001b[6nthree\nfour\n");
  process.exit();
  const restarted = await value.manager.restart(
    request(value.root, { cols: 90, rows: 25 }),
  );
  assert.equal(restarted.history, "");
  assert.equal(value.processes.length, 2);

  value.processes[1].data("one\ntwo\n\u001b[6nthree\nfour\n");
  await new Promise((resolve) => setTimeout(resolve, 60));
  value.processes[1].exit();
  const replacement = createTerminalManager({
    logsDir: path.join(value.root, ".history"),
    getLocalThread: () => ({ worktreePath: value.root }),
    pty: { spawn: () => new FakeProcess(999) },
    env: { PATH: "/usr/bin" },
    inspect: async () => null,
    historyLines: 3,
    killGraceMs: 1,
  });
  t.after(() => replacement.shutdown());
  const restored = await replacement.attach(request(value.root), () => {});
  assert.equal(restored.snapshot.history, "two\nthree\nfour\n");

  attached.detach();
  await replacement.close({
    localSessionId: "acp-1",
    terminalId: "term-1",
    deleteHistory: true,
  });
  assert.deepEqual(replacement.list("acp-1"), []);
});

test("deleting a local session stops its terminals and removes their history", async (t) => {
  const value = fixture();
  t.after(() => value.manager.shutdown());
  await value.manager.open(request(value.root));
  value.processes[0].data("saved output\n");

  await value.manager.deleteSession("acp-1");

  assert.deepEqual(value.manager.list("acp-1"), []);
  assert.equal(value.processes[0].kills[0], "SIGTERM");
  const attached = await value.manager.attach(request(value.root), () => {});
  assert.equal(attached.snapshot.history, "");
  attached.detach();
});

test("drains queued and trailing PTY output before publishing exit", async (t) => {
  const value = fixture();
  t.after(() => value.manager.shutdown());
  const events = [];
  await value.manager.attach(request(value.root), (event) =>
    events.push(event),
  );
  const process = value.processes[0];

  process.data("queued\n");
  process.exit({ exitCode: 3, signal: 0 });
  process.data("after-exit-callback\n");
  await tick();

  assert.deepEqual(
    events.map((event) => event.type),
    ["output", "exited"],
  );
  assert.equal(events[0].data, "queued\n");
  assert.match(
    (await value.manager.attach(request(value.root), () => {})).snapshot
      .history,
    /queued/,
  );
});

test("retains every running session and limits only inactive sessions", async (t) => {
  const value = fixture({ maxSessions: 1 });
  t.after(() => value.manager.shutdown());

  await value.manager.open(request(value.root, { terminalId: "term-1" }));
  await value.manager.open(request(value.root, { terminalId: "term-2" }));
  assert.equal(value.manager.list("acp-1").length, 2);
  assert.equal(value.processes[0].kills.length, 0);

  value.processes[0].exit();
  value.processes[1].exit();
  await tick();
  assert.equal(value.manager.list("acp-1").length, 1);
});

test("serializes lifecycle per local thread and coalesces pending resize to the latest size", async (t) => {
  const value = fixture();
  t.after(() => value.manager.shutdown());
  await value.manager.open(request(value.root));

  const restart = value.manager.restart(
    request(value.root, { terminalId: "term-2" }),
  );
  const first = value.manager.resize(
    request(value.root, { cols: 80, rows: 20 }),
  );
  const second = value.manager.resize(
    request(value.root, { cols: 100, rows: 30 }),
  );
  const third = value.manager.resize(
    request(value.root, { cols: 140, rows: 50 }),
  );
  await Promise.all([restart, first, second, third]);

  assert.equal(value.processes.length, 2);
  assert.deepEqual(value.processes[0].resizes, [{ cols: 140, rows: 50 }]);
});

test("uses zsh nopromptsp and falls back only for missing executables", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-terminal-"));
  const spawnInputs = [];
  let attempts = 0;
  const adapter = {
    spawn(shell, args, input) {
      spawnInputs.push({ shell, args, input });
      if (attempts++ === 0) throw new Error("spawn /custom/zsh ENOENT");
      return new FakeProcess(200);
    },
  };
  const manager = createTerminalManager({
    logsDir: path.join(root, ".history"),
    getLocalThread: () => ({ worktreePath: root }),
    pty: adapter,
    env: { SHELL: "/custom/zsh", PATH: "/usr/bin" },
    inspect: async () => null,
    killGraceMs: 1,
  });
  t.after(() => manager.shutdown());
  await manager.open(request(root));
  assert.deepEqual(
    spawnInputs.map(({ shell, args }) => ({ shell, args })),
    [
      { shell: "/custom/zsh", args: ["-o", "nopromptsp"] },
      { shell: "/bin/zsh", args: ["-o", "nopromptsp"] },
    ],
  );

  const fatalInputs = [];
  const fatal = createTerminalManager({
    logsDir: path.join(root, ".history-fatal"),
    getLocalThread: () => ({ worktreePath: root }),
    pty: {
      spawn: (shell) => {
        fatalInputs.push(shell);
        throw new Error("permission denied");
      },
    },
    env: { SHELL: "/custom/zsh" },
    inspect: async () => null,
    killGraceMs: 1,
  });
  t.after(() => fatal.shutdown());
  await assert.rejects(fatal.open(request(root)), /permission denied/);
  assert.deepEqual(fatalInputs, ["/custom/zsh"]);
});

test("scrubs AppImage markers and mount paths from spawned terminals", async (t) => {
  const appDir = "/tmp/.mount_OpenSWE";
  const value = fixture({
    env: {
      APPIMAGE: "/home/user/OpenSWE.AppImage",
      APPDIR: appDir,
      ARGV0: "/home/user/OpenSWE.AppImage",
      OWD: "/home/user",
      PATH: `${appDir}/usr/bin:/usr/bin`,
      LD_LIBRARY_PATH: `${appDir}/usr/lib:/host/lib`,
      XDG_DATA_DIRS: `${appDir}/usr/share:/usr/share`,
      GSETTINGS_SCHEMA_DIR: `${appDir}/schemas`,
    },
  });
  t.after(() => value.manager.shutdown());
  await value.manager.open(request(value.root));
  const env = value.spawnInputs[0].input.env;

  for (const key of ["APPIMAGE", "APPDIR", "ARGV0", "OWD"])
    assert.equal(env[key], undefined);
  assert.equal(env.PATH, "/usr/bin");
  assert.equal(env.LD_LIBRARY_PATH, "/host/lib");
  assert.equal(env.XDG_DATA_DIRS, "/usr/share");
  assert.equal(env.GSETTINGS_SCHEMA_DIR, undefined);
});

test("repairs node-pty spawn helpers in prebuild and build layouts", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-node-pty-"));
  const unixTerminalPath = path.join(root, "lib", "unixTerminal.js");
  const helpers = [
    path.join(root, "prebuilds", "linux-x64", "spawn-helper"),
    path.join(root, "build", "Release", "spawn-helper"),
    path.join(root, "build", "Debug", "spawn-helper"),
  ];
  fs.mkdirSync(path.dirname(unixTerminalPath), { recursive: true });
  fs.writeFileSync(unixTerminalPath, "");
  for (const helper of helpers) {
    fs.mkdirSync(path.dirname(helper), { recursive: true });
    fs.writeFileSync(helper, "");
    fs.chmodSync(helper, 0o644);
  }

  ensurePtySpawnHelperExecutable({
    platform: "linux",
    arch: "x64",
    unixTerminalPath,
  });

  for (const helper of helpers) assert.ok(fs.statSync(helper).mode & 0o111);
});
