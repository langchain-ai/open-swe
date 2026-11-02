import fs = require("node:fs/promises");
import path = require("node:path");
import { createHash, randomUUID } from "node:crypto";
const { SESSION_COOKIE_NAME } = require("./config.cjs");

type SkillTarget = {
  url: string;
  session: string;
};

type SkillSyncOptions = {
  home: string;
  statePath: string;
  target: () => Promise<SkillTarget | null>;
  request: (url: string, init: RequestInit) => Promise<Response>;
  log: (message: string, error: unknown) => void;
};

class SkillSync {
  private readonly uploaded = new Map<string, string>();
  private readonly readErrors = new Map<string, string>();
  private readonly inventories = new Map<string, string>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private running: Promise<void> | null = null;
  private stopped = false;

  constructor(private readonly options: SkillSyncOptions) {}

  start(): void {
    this.stopped = false;
    this.timer ??= setInterval(() => void this.sync(), 10_000);
    this.timer.unref();
    void this.sync();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  async sync(): Promise<void> {
    this.running ??= this.upload()
      .catch((error: unknown) => {
        this.options.log("Could not sync desktop skills", error);
      })
      .finally(() => {
        this.running = null;
      });
    await this.running;
  }

  private async readFiles(
    root: string,
    directory = root,
  ): Promise<Record<string, string>> {
    if (path.relative(root, directory).split(path.sep).length > 10)
      throw new Error(`Skill resources are nested too deeply: ${root}`);
    const files: Record<string, string> = {};
    const entries = await fs.readdir(directory, { withFileTypes: true });
    for (const entry of entries.sort((left, right) =>
      left.name.localeCompare(right.name),
    )) {
      if (entry.name.startsWith(".")) continue;
      const filename = path.join(directory, entry.name);
      if (entry.isSymbolicLink())
        throw new Error(`Skill resource is a symlink: ${filename}`);
      if (entry.isDirectory())
        Object.assign(files, await this.readFiles(root, filename));
      else if (entry.isFile()) {
        if ((await fs.stat(filename)).size > 200_000)
          throw new Error(`Skill resource is too large: ${filename}`);
        files[path.relative(root, filename).split(path.sep).join("/")] =
          new TextDecoder("utf-8", { fatal: true }).decode(
            await fs.readFile(filename),
          );
      }
      if (
        Object.keys(files).length > 100 ||
        JSON.stringify(files).length > 1_000_000
      )
        throw new Error(`Skill bundle is too large: ${root}`);
    }
    return files;
  }

  private async sourceId(): Promise<string> {
    try {
      const id = (await fs.readFile(this.options.statePath, "utf8")).trim();
      if (!/^[0-9a-f-]{36}$/.test(id))
        throw new Error("Invalid skill sync source ID");
      return id;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      const id = randomUUID();
      await fs.mkdir(path.dirname(this.options.statePath), { recursive: true });
      await fs.writeFile(this.options.statePath, id, {
        flag: "wx",
        mode: 0o600,
      });
      return id;
    }
  }

  private async upload(): Promise<void> {
    const target = await this.options.target();
    if (!target || this.stopped) return;
    const sourceId = await this.sourceId();
    const seen = new Set<string>();
    for (const folder of [".agent", ".agents"]) {
      const root = path.join(this.options.home, folder, "skills");
      let entries: string[];
      try {
        entries = await fs.readdir(root);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") continue;
        throw error;
      }
      for (const name of entries.sort()) {
        if (name.startsWith(".") || seen.has(name)) continue;
        seen.add(name);
        const directory = path.join(root, name);
        try {
          if (!(await fs.stat(directory)).isDirectory()) continue;
          const files = await this.readFiles(directory);
          this.readErrors.delete(directory);
          if (!Object.hasOwn(files, "SKILL.md")) {
            seen.delete(name);
            continue;
          }
          const body = JSON.stringify({ files, source_id: sourceId });
          const key = `${target.url}\n${target.session}\n${name}`;
          const hash = createHash("sha256").update(body).digest("hex");
          if (this.uploaded.get(key) === hash) continue;
          const current = await this.options.target();
          if (
            this.stopped ||
            current?.url !== target.url ||
            current.session !== target.session
          )
            return;
          const response = await this.options.request(
            `${target.url}/dashboard/api/skills/${encodeURIComponent(name)}/import`,
            {
              method: "PUT",
              headers: {
                "content-type": "application/json",
                cookie: `${SESSION_COOKIE_NAME}=${target.session}`,
              },
              body,
              redirect: "error",
              signal: AbortSignal.timeout(15_000),
            },
          );
          if (
            response.status >= 400 &&
            response.status < 500 &&
            ![408, 429].includes(response.status)
          ) {
            this.uploaded.set(key, hash);
            this.options.log(
              `Desktop skill needs changes before upload: ${name}`,
              new Error(`Skill upload rejected (${response.status})`),
            );
            continue;
          }
          if (!response.ok)
            throw new Error(
              `Skill upload rejected (${response.status}): ${name}`,
            );
          this.uploaded.set(key, hash);
        } catch (error) {
          const message =
            error instanceof Error ? error.message : String(error);
          if (this.readErrors.get(directory) !== message) {
            this.readErrors.set(directory, message);
            this.options.log(`Could not sync desktop skill: ${name}`, error);
          }
        }
      }
    }
    const current = await this.options.target();
    if (
      this.stopped ||
      current?.url !== target.url ||
      current.session !== target.session
    )
      return;
    const inventory = JSON.stringify({ source_id: sourceId, names: [...seen] });
    const inventoryKey = `${target.url}\n${target.session}`;
    if (this.inventories.get(inventoryKey) === inventory) return;
    const response = await this.options.request(
      `${target.url}/dashboard/api/skills/import-inventory`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          cookie: `${SESSION_COOKIE_NAME}=${target.session}`,
        },
        body: inventory,
        redirect: "error",
        signal: AbortSignal.timeout(15_000),
      },
    );
    if (!response.ok)
      throw new Error(`Skill inventory rejected (${response.status})`);
    this.inventories.set(inventoryKey, inventory);
  }
}

export { SkillSync };
