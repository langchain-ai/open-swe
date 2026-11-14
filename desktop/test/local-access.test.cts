const test = require("node:test");
const assert = require("node:assert/strict");
const {
  mkdir,
  mkdtemp,
  readFile,
  rm,
  symlink,
  writeFile,
} = require("node:fs/promises");
const { tmpdir } = require("node:os");
const { dirname, join } = require("node:path");
const { LocalAccessBridge } = require("../build/local-access.cjs");

import type { BridgeSender, JsonObject } from "open-swe-bridge-client";

type ActionReply = { result?: JsonObject; error?: string };
type QueuedAction = {
  request_id: string;
  method: string;
  params: JsonObject;
};

class LocalAccessHarness {
  requests: QueuedAction[] = [];
  /** Each action's reply, by its request id: replies can arrive in any order. */
  answers = new Map<string, (reply: ActionReply) => void>();
  waiters: (() => void)[] = [];
  approvals: string[] = [];
  offered: (string | null)[] = [];
  allow = false;
  choice: "deny" | "once" | "grant" | null = null;
  /** A thread whose approvals wait until `release` is called. */
  holdThread: string | null = null;
  release: () => void = () => {};
  held = new Promise<void>((done) => (this.release = done));
  bridge: InstanceType<typeof LocalAccessBridge>;

  constructor(home?: string) {
    this.bridge = new LocalAccessBridge({
      send: this.send,
      env: () => (home ? { ...process.env, HOME: home } : process.env),
      approve: async ({
        threadId,
        threadTitle,
        concern,
        detail,
        grantFolder,
      }: {
        threadId: string | null;
        threadTitle: string | null;
        concern: string | null;
        detail: string;
        grantFolder: string | null;
      }) => {
        if (threadId && threadId === this.holdThread) await this.held;
        // Everything the person is shown, as one string for assertions.
        this.approvals.push(
          [threadTitle, concern, detail].filter(Boolean).join("\n\n"),
        );
        this.offered.push(grantFolder);
        return this.choice ?? (this.allow ? "once" : "deny");
      },
      log: () => {},
    });
  }

  send: BridgeSender = async (method, path, options) => {
    if (path.endsWith("/claim")) {
      if (!this.requests.length) {
        await new Promise<void>((done, reject) => {
          this.waiters.push(done);
          options.signal?.addEventListener(
            "abort",
            () => reject(new Error("closed")),
            { once: true },
          );
        });
      }
      return { requests: this.requests.splice(0) };
    }
    if (path.endsWith("/bridges") && method === "POST")
      return { bridge_id: "approved", heartbeat_interval_seconds: 3600 };
    if (path.includes("/requests/")) {
      const id = decodeURIComponent(path.split("/requests/")[1].split("/")[0]);
      const answer = this.answers.get(id);
      if (!answer) throw new Error("Unexpected action response");
      this.answers.delete(id);
      answer(options.body as ActionReply);
    }
    return {};
  };

  async action(method: string, params: JsonObject): Promise<ActionReply> {
    const requestId = String(Math.random());
    const result = new Promise<ActionReply>((done) =>
      this.answers.set(requestId, done),
    );
    this.requests.push({
      request_id: requestId,
      method,
      params: { expires_at: Date.now() / 1000 + 120, timeout: 120, ...params },
    });
    for (const wake of this.waiters.splice(0)) wake();
    return result;
  }
}

test("denied local writes and commands have no effects; approved writes are exact", async () => {
  const root = await mkdtemp(join(tmpdir(), "robin-local-access-"));
  const target = join(root, "file.txt");
  const harness = new LocalAccessHarness();
  await writeFile(target, "original");
  await harness.bridge.ensure();
  try {
    const denied = await harness.action("local_action", {
      operation: "write",
      path: target,
      content: "replacement",
    });
    assert.match(denied.error, /denied/);
    assert.equal(await readFile(target, "utf8"), "original");
    const command = await harness.action("local_action", {
      operation: "execute",
      cwd: root,
      command: "printf changed > file.txt",
    });
    assert.match(command.error, /denied/);
    assert.equal(await readFile(target, "utf8"), "original");
    harness.allow = true;
    const legacy = await harness.action("execute", {
      command: "printf changed > file.txt",
    });
    assert.match(legacy.error, /individually approved/);
    assert.equal(await readFile(target, "utf8"), "original");
    const allowed = await harness.action("local_action", {
      operation: "write",
      path: target,
      content: "replacement",
    });
    assert.equal(allowed.result.written, true);
    assert.equal(await readFile(target, "utf8"), "replacement");
    assert.ok(harness.approvals.at(-1).includes("replacement"));
    const expired = await harness.action("local_action", {
      operation: "write",
      path: target,
      content: "expired",
      expires_at: 1,
    });
    assert.match(expired.error, /expired/);
    assert.equal(await readFile(target, "utf8"), "replacement");
  } finally {
    await harness.bridge.close();
    await rm(root, { recursive: true });
  }
});

test("approvals show hidden characters and refuse commands too long to review", async () => {
  const root = await mkdtemp(join(tmpdir(), "robin-local-access-"));
  const harness = new LocalAccessHarness();
  await harness.bridge.ensure();
  try {
    const spoofed = await harness.action("local_action", {
      operation: "execute",
      cwd: root,
      command: "echo safe ‮; rm -rf ~",
    });
    assert.match(spoofed.error, /denied/);
    assert.ok(harness.approvals.at(-1).includes("\\u{202e}"));
    assert.ok(!harness.approvals.at(-1).includes("‮"));
    const approvals = harness.approvals.length;
    const long = await harness.action("local_action", {
      operation: "execute",
      cwd: root,
      command: `echo ${"x".repeat(5000)}`,
    });
    assert.match(long.error, /too long to review/);
    assert.equal(harness.approvals.length, approvals);
    await harness.action("local_action", {
      operation: "write",
      path: join(root, "big.txt"),
      content: "y".repeat(10000),
    });
    // The approval panel scrolls, so a write's whole content is shown.
    assert.ok(harness.approvals.at(-1).includes("y".repeat(10000)));
  } finally {
    await harness.bridge.close();
    await rm(root, { recursive: true });
  }
});

test("~ paths resolve under the person's home, and import returns whole binary files", async () => {
  const home = await mkdtemp(join(tmpdir(), "robin-local-home-"));
  const harness = new LocalAccessHarness(home);
  harness.allow = true;
  const binary = Buffer.alloc(100 * 1024, 0xff);
  await writeFile(join(home, "note.txt"), "hello");
  await writeFile(join(home, "data.bin"), binary);
  await harness.bridge.ensure();
  try {
    const read = await harness.action("local_action", {
      operation: "read",
      path: "~/note.txt",
    });
    assert.equal(read.result.content, "hello");
    assert.ok(harness.approvals.at(-1).includes("~/note.txt"));
    const tooBig = await harness.action("local_action", {
      operation: "read",
      path: "~/data.bin",
    });
    assert.match(tooBig.error, /64 KiB read limit/);
    const imported = await harness.action("local_action", {
      operation: "import",
      path: "~/data.bin",
    });
    assert.equal(imported.result.size, binary.length);
    assert.ok(
      Buffer.from(imported.result.content_base64, "base64").equals(binary),
    );
    const relative = await harness.action("local_action", {
      operation: "read",
      path: "note.txt",
    });
    assert.match(relative.error, /absolute or start with ~\//);
  } finally {
    await harness.bridge.close();
    await rm(home, { recursive: true });
  }
});

test("a thread grant runs cleared actions in its checkout without asking, and nothing else", async () => {
  const home = await mkdtemp(join(tmpdir(), "robin-local-grant-"));
  const harness = new LocalAccessHarness(home);
  await mkdir(join(home, "code/demo/src"), { recursive: true });
  await mkdir(join(home, "code/demo/.git"));
  await writeFile(join(home, "code/demo/src/app.py"), "print(1)");
  await writeFile(join(home, "code/demo/notes.md"), "notes");
  await writeFile(join(home, "top.txt"), "top");
  await harness.bridge.ensure();
  const thread = {
    thread_id: "t1",
    thread_title: "Finish the TODO",
    cleared: true,
  };
  try {
    harness.choice = "grant";
    const first = await harness.action("local_action", {
      ...thread,
      operation: "read",
      path: "~/code/demo/src/app.py",
    });
    assert.equal(first.result.content, "print(1)");
    // The grant covers the whole checkout, not just the file's folder.
    assert.ok(harness.offered.at(-1).endsWith("/code/demo"));
    assert.ok(harness.approvals.at(-1).includes("Finish the TODO"));

    harness.choice = "deny";
    const asked = harness.approvals.length;
    const inside = await harness.action("local_action", {
      ...thread,
      operation: "read",
      path: "~/code/demo/notes.md",
    });
    assert.equal(inside.result.content, "notes");
    assert.equal(harness.approvals.length, asked);

    for (const params of [
      {
        ...thread,
        cleared: false,
        concern: "exfiltration",
        operation: "read",
        path: "~/code/demo/notes.md",
      },
      {
        ...thread,
        thread_id: "t2",
        operation: "read",
        path: "~/code/demo/notes.md",
      },
      { ...thread, operation: "read", path: "~/top.txt" },
    ]) {
      const denied = await harness.action("local_action", params);
      assert.match(denied.error, /denied/);
    }
    assert.equal(harness.approvals.length, asked + 3);
    assert.match(harness.approvals.at(-3), /send data off this Mac/);
    assert.equal(harness.offered.at(-1), null);

    // A link inside the checkout to a credential file still asks.
    await writeFile(join(home, "code/demo/.env"), "TOKEN=secret");
    await symlink(".env", join(home, "code/demo/config.txt"));
    const viaLink = await harness.action("local_action", {
      ...thread,
      operation: "read",
      path: "~/code/demo/config.txt",
    });
    assert.match(viaLink.error, /denied/);
    assert.equal(harness.approvals.length, asked + 4);

    // No grant is offered for a folder above home.
    const above = join(dirname(home), `robin-above-home-${Date.now()}.txt`);
    await writeFile(above, "above");
    try {
      await harness.action("local_action", {
        ...thread,
        thread_id: "t3",
        operation: "read",
        path: above,
      });
      assert.equal(harness.offered.at(-1), null);
    } finally {
      await rm(above);
    }

    harness.bridge.grants.revoke("t1");
    await harness.action("local_action", {
      ...thread,
      operation: "read",
      path: "~/code/demo/notes.md",
    });
    assert.equal(harness.approvals.length, asked + 6);
  } finally {
    await harness.bridge.close();
    await rm(home, { recursive: true });
  }
});

const { ApprovalQueue } = require("../build/approval-queue.cjs");

test("only a decision by id settles a request; an abort denies it", async () => {
  let changes = 0;
  const queue = new ApprovalQueue(() => (changes += 1));
  const request = {
    kind: "read",
    threadId: "t1",
    threadTitle: "Thread",
    concern: null,
    detail: "Read this exact local file?",
    grantFolder: null,
  };
  const decided = queue.request(request, new AbortController().signal);
  const aborted = new AbortController();
  const expiring = queue.request(
    { ...request, threadId: "t2" },
    aborted.signal,
  );
  assert.equal(queue.list("t1").length, 1);
  assert.equal(queue.list().length, 2);
  const [first] = queue.list("t1");
  assert.equal(queue.first().id, first.id);
  assert.equal(queue.decide("not-a-request", "once"), false);
  assert.equal(queue.decide(first.id, "grant"), true);
  assert.equal(await decided, "grant");
  aborted.abort();
  assert.equal(await expiring, "deny");
  assert.equal(queue.list().length, 0);
  assert.equal(queue.decide(first.id, "once"), false);
  assert.ok(changes >= 4);
});

test("a thread waiting on approval doesn't hold another thread's actions", async () => {
  const home = await mkdtemp(join(tmpdir(), "robin-local-threads-"));
  const harness = new LocalAccessHarness(home);
  harness.choice = "once";
  harness.holdThread = "slow";
  await writeFile(join(home, "note.txt"), "hello");
  await harness.bridge.ensure();
  try {
    const slow = harness.action("local_action", {
      thread_id: "slow",
      operation: "read",
      path: "~/note.txt",
    });
    const fast = await harness.action("local_action", {
      thread_id: "fast",
      operation: "read",
      path: "~/note.txt",
    });
    assert.equal(fast.result.content, "hello");
    harness.release();
    assert.equal((await slow).result.content, "hello");
  } finally {
    await harness.bridge.close();
    await rm(home, { recursive: true });
  }
});
