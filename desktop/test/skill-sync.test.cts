const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { SkillSync } = require("../build/skill-sync.cjs");

test("syncs changed bundles to the current account, retries failures, and preserves cloud deletions", async (t) => {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), "skill-sync-"));
  t.after(() => fs.rm(home, { recursive: true, force: true }));
  const directory = path.join(home, ".agent/skills/review");
  await fs.mkdir(path.join(directory, "references"), { recursive: true });
  await fs.writeFile(
    path.join(directory, "SKILL.md"),
    "---\nname: review\ndescription: Review code\n---\nReview it.",
  );
  await fs.writeFile(
    path.join(directory, "references/checklist.md"),
    "Check auth.",
  );
  const requests: { url: string; body: string; cookie: string }[] = [];
  let session = "alice";
  let status = 503;
  const sync = new SkillSync({
    home,
    target: async () =>
      session ? { url: "https://example.com", session } : null,
    request: async (url: string, init: RequestInit) => {
      requests.push({
        url,
        body: String(init.body),
        cookie: new Headers(init.headers).get("cookie")!,
      });
      return new Response("", { status });
    },
    log: () => {},
  });
  await sync.sync();
  status = 200;
  await sync.sync();
  await sync.sync();
  assert.equal(requests.length, 2);
  assert.equal(
    JSON.parse(requests[1].body).files["references/checklist.md"],
    "Check auth.",
  );
  await fs.writeFile(
    path.join(directory, "references/checklist.md"),
    "Check permissions.",
  );
  await sync.sync();
  assert.equal(requests.length, 3);
  session = "bob";
  await sync.sync();
  assert.equal(requests[3].cookie, "osw_session=bob");
  session = "";
  await sync.sync();
  assert.equal(requests.length, 4);
  await fs.rm(directory, { recursive: true });
  session = "bob";
  await sync.sync();
  assert.equal(requests.length, 4);
});

test("discovers standard .agents skills but gives the requested .agent root priority", async (t) => {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), "skill-sync-"));
  t.after(() => fs.rm(home, { recursive: true, force: true }));
  for (const folder of [".agent", ".agents"]) {
    const directory = path.join(home, folder, "skills/review");
    await fs.mkdir(directory, { recursive: true });
    await fs.writeFile(path.join(directory, "SKILL.md"), folder);
  }
  const uploaded: string[] = [];
  const sync = new SkillSync({
    home,
    target: async () => ({ url: "https://example.com", session: "alice" }),
    request: async (_url: string, init: RequestInit) => {
      uploaded.push(JSON.parse(String(init.body)).files["SKILL.md"]);
      return new Response();
    },
    log: () => {},
  });
  await sync.sync();
  assert.deepEqual(uploaded, [".agent"]);
});
