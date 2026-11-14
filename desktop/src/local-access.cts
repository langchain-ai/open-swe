import { constants } from "node:fs";
import { open, realpath, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, join } from "node:path";
import {
  Bridge,
  BridgeHttpApi,
  LocalExecutor,
  type BridgeSender,
  type JsonObject,
} from "open-swe-bridge-client";

/** What the person chose in an approval dialog. */
type Decision = "deny" | "once" | "grant";

/** What a local action asks of the person; the approval panel shows it. */
interface ApprovalRequest {
  kind: "read" | "write" | "import" | "execute";
  threadId: string | null;
  threadTitle: string | null;
  /** Why the runtime's reviewer wants the person to look, when it does. */
  concern: string | null;
  /** The exact action: path and contents, or working folder and command. */
  detail: string;
  /** The folder a thread grant would cover, when one may be offered. */
  grantFolder: string | null;
}

interface LocalAccessOptions {
  send: BridgeSender;
  env: () => Record<string, string | undefined>;
  approve: (request: ApprovalRequest, signal: AbortSignal) => Promise<Decision>;
  log: (message: string) => void;
  /** Called when a grant starts, ends or expires. */
  onGrantsChanged?: () => void;
}

/** One thread's standing approval for a folder, until it expires. */
interface Grant {
  threadId: string;
  threadTitle: string | null;
  folder: string;
  until: number;
}

/** What the runtime made of an action, and which thread asked for it. */
interface Scope {
  threadId: string | null;
  threadTitle: string | null;
  cleared: boolean;
  concern: string | null;
}

const GRANT_MS = 30 * 60 * 1000;

// Where credentials live, as the runtime's reviewer lists them (`mda/rt/auto_review.py`),
// with shell profiles, which often export tokens.
const CREDENTIAL_PARTS = new Set([
  ".git",
  ".ssh",
  ".aws",
  ".azure",
  ".gnupg",
  ".kube",
  ".docker",
  ".password-store",
  ".1password",
  "gcloud",
  "keychains",
  ".netrc",
  ".npmrc",
  ".pypirc",
  ".pgpass",
  ".git-credentials",
  ".zsh_history",
  ".bash_history",
  ".zshrc",
  ".zshenv",
  ".zprofile",
  ".bashrc",
  ".bash_profile",
  ".profile",
]);
const CREDENTIAL_EXTENSIONS = new Set([
  "pem",
  "key",
  "p12",
  "pfx",
  "jks",
  "keystore",
  "kdbx",
  "tfvars",
  "tfstate",
]);

// What the runtime's reviewer flags an action for, as the dialog says it.
const CONCERNS: Record<string, string> = {
  credential_access: "it may read or use credentials",
  exfiltration: "it may send data off this Mac",
  destructive: "it may delete or overwrite data",
  persistence: "it may set up something that keeps running",
  security_bypass: "it may weaken a security setting",
  scope_escalation: "it reaches beyond what you asked for",
  too_long: "it is too long to review automatically",
  unsure: "the reviewer could not tell that it is routine",
};

/**
 * Folders a person let one thread act in without asking, for a while. Kept only in
 * this process: quitting, signing out or closing local access ends every grant.
 */
class ThreadGrants {
  private grants: Grant[] = [];
  private readonly timers = new Set<NodeJS.Timeout>();

  constructor(private readonly changed: () => void = () => {}) {}

  /**
   * Whether a resolved path may hold credentials. A grant never covers one: it is
   * checked where the file really is, so a link inside the folder can't hide it.
   */
  static holdsCredentials(path: string): boolean {
    const parts = path.toLowerCase().split("/");
    const name = parts.at(-1) ?? "";
    const extension = name.includes(".") ? name.split(".").at(-1) : "";
    return (
      parts.some(
        (part) =>
          CREDENTIAL_PARTS.has(part) ||
          part.startsWith(".env") ||
          part.startsWith("credentials"),
      ) || CREDENTIAL_EXTENSIONS.has(extension ?? "")
    );
  }

  /** The live grant of `threadId` whose folder holds `path`. */
  covering(threadId: string, path: string): Grant | undefined {
    this.prune();
    return this.grants.find(
      (grant) =>
        grant.threadId === threadId &&
        (path === grant.folder || path.startsWith(`${grant.folder}/`)),
    );
  }

  add(threadId: string, threadTitle: string | null, folder: string): void {
    this.grants = this.grants.filter(
      (grant) => grant.threadId !== threadId || grant.folder !== folder,
    );
    this.grants.push({
      threadId,
      threadTitle,
      folder,
      until: Date.now() + GRANT_MS,
    });
    const timer = setTimeout(() => {
      this.timers.delete(timer);
      this.prune();
      this.changed();
    }, GRANT_MS);
    timer.unref();
    this.timers.add(timer);
    this.changed();
  }

  list(threadId?: string): Grant[] {
    this.prune();
    return this.grants.filter(
      (grant) => !threadId || grant.threadId === threadId,
    );
  }

  /** Ends a thread's grant for `folder`, or every grant it has. */
  revoke(threadId: string, folder?: string): void {
    this.grants = this.grants.filter(
      (grant) =>
        grant.threadId !== threadId ||
        (folder !== undefined && grant.folder !== folder),
    );
    this.changed();
  }

  clear(): void {
    for (const timer of this.timers) clearTimeout(timer);
    this.timers.clear();
    this.grants = [];
    this.changed();
  }

  private prune(): void {
    const now = Date.now();
    this.grants = this.grants.filter((grant) => grant.until > now);
  }
}

// The approval panel scrolls; text past what a write may carry is cut, and the cut is said.
const PREVIEW_LIMIT = 64 * 1024;
// A command must be reviewable in full; write longer ones to a file first.
const COMMAND_LIMIT = 4000;
// A read goes into the model's context; an import goes into the sandbox, as an upload does.
const READ_LIMIT = 64 * 1024;
const IMPORT_LIMIT = 10 * 1024 * 1024;

/** Text as an approval dialog shows it. */
class DialogText {
  /**
   * Control, bidi and invisible characters become visible escapes, so what is
   * approved reads as what will run.
   */
  static shown(text: string): string {
    return text.replace(
      // oxlint-disable-next-line no-control-regex -- escapes them for review
      /[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u061c\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]/g,
      (char) => `\\u{${char.codePointAt(0)!.toString(16).padStart(4, "0")}}`,
    );
  }

  /** Long text cut to what a native dialog can show, saying what was cut. */
  static preview(text: string): string {
    const visible = DialogText.shown(text);
    if (visible.length <= PREVIEW_LIMIT) return visible;
    return `${visible.slice(0, PREVIEW_LIMIT)}\n\n[${visible.length - PREVIEW_LIMIT} more characters not shown; ${Buffer.byteLength(text)} bytes in all]`;
  }
}

class LocalAccessBridge {
  private bridge: Bridge | null = null;
  private opening: Promise<string> | null = null;
  private closed = false;
  private shutdown = new AbortController();
  /** Each thread's actions run in order; one thread's waiting action never holds another's. */
  private readonly pending = new Map<string, Promise<unknown>>();
  private readonly executors = new Set<LocalExecutor>();
  readonly grants: ThreadGrants;

  constructor(private readonly options: LocalAccessOptions) {
    this.grants = new ThreadGrants(options.onGrantsChanged);
  }

  async ensure(): Promise<string> {
    if (this.closed) throw new Error("Local access is closed");
    if (this.bridge?.running) return this.bridge.session.bridgeId;
    this.opening ??= this.connect().finally(() => {
      this.opening = null;
    });
    return this.opening;
  }

  private async connect(): Promise<string> {
    this.shutdown = new AbortController();
    const bridge = await Bridge.open(new BridgeHttpApi(this.options.send), {
      client: "desktop",
      rootPath: "local-access://approved-actions",
      label: "Approved local actions",
      bridgeId: null,
      credentialRejected: "Sign in again to offer local access.",
      log: this.options.log,
      handlers: {
        local_action: (params) => this.enqueue(params),
        execute: () => this.refuse(),
        upload_files: () => this.refuse(),
        download_files: () => this.refuse(),
        worktree_handoff: () => this.refuse(),
      },
    });
    if (this.closed) {
      await bridge.close();
      throw new Error("Local access is closed");
    }
    this.bridge = bridge;
    bridge.start((error) => {
      this.options.log(String(error));
      this.shutdown.abort();
      this.bridge = null;
      for (const executor of this.executors) executor.stopAll();
    });
    return bridge.session.bridgeId;
  }

  private async refuse(): Promise<JsonObject> {
    throw new Error("Only individually approved local actions are supported");
  }

  private async enqueue(params: Record<string, unknown>): Promise<JsonObject> {
    const bridge = this.bridge;
    const thread = typeof params.thread_id === "string" ? params.thread_id : "";
    const previous = this.pending.get(thread) ?? Promise.resolve();
    const action = previous.then(() => this.perform(params, bridge));
    const settled = action.catch(() => undefined);
    this.pending.set(thread, settled);
    void settled.then(() => {
      if (this.pending.get(thread) === settled) this.pending.delete(thread);
    });
    return action;
  }

  private text(params: Record<string, unknown>, key: string): string {
    const value = params[key];
    if (
      typeof value !== "string" ||
      value.includes("\0") ||
      Buffer.byteLength(value) > 65536
    )
      throw new Error(`Invalid ${key}`);
    return value;
  }

  private scope(params: Record<string, unknown>): Scope {
    const text = (key: string) =>
      typeof params[key] === "string" && params[key]
        ? String(params[key])
        : null;
    return {
      threadId: text("thread_id"),
      threadTitle: text("thread_title"),
      cleared: params.cleared === true,
      concern: text("concern"),
    };
  }

  /**
   * Runs at once inside a grant the thread holds when the runtime cleared the action;
   * otherwise asks the person, offering a grant for `folder` unless that is too broad.
   */
  private async approve(
    kind: ApprovalRequest["kind"],
    detail: string,
    folder: string,
    target: string | null,
    scope: Scope,
    expiresAt: number,
    bridge: Bridge | null,
  ): Promise<void> {
    if (this.closed || !bridge?.running || this.bridge !== bridge)
      throw new Error("Local access is offline");
    if (Date.now() / 1000 >= expiresAt) throw new Error("Local action expired");
    if (
      scope.threadId &&
      scope.cleared &&
      !(target && ThreadGrants.holdsCredentials(target)) &&
      this.grants.covering(scope.threadId, folder)
    ) {
      this.options.log(
        `Ran inside a thread grant: ${detail.split("\n\n").slice(0, 2).join(" ")}`,
      );
      return;
    }
    // Compared as resolved, like `folder`: on a Mac, /var is /private/var.
    const home = await realpath(this.options.env().HOME || homedir()).catch(
      () => this.options.env().HOME || homedir(),
    );
    const covers = await this.checkoutOf(folder, home);
    // No grant reaches the home folder: not home itself, nor any folder above it.
    const grantFolder =
      scope.threadId && covers !== "/" && !`${home}/`.startsWith(`${covers}/`)
        ? covers
        : null;
    const decision = await this.options.approve(
      {
        kind,
        threadId: scope.threadId,
        threadTitle: scope.threadTitle,
        concern: scope.cleared
          ? null
          : (CONCERNS[scope.concern ?? "unsure"] ?? CONCERNS.unsure),
        detail,
        grantFolder,
      },
      AbortSignal.any([
        this.shutdown.signal,
        AbortSignal.timeout(
          Math.max(1, Math.ceil(expiresAt * 1000 - Date.now())),
        ),
      ]),
    );
    if (decision === "deny") throw new Error("Local action denied");
    if (this.closed || !bridge?.running || this.bridge !== bridge)
      throw new Error("Local access is offline");
    if (Date.now() / 1000 >= expiresAt) throw new Error("Local action expired");
    if (decision === "grant" && grantFolder && scope.threadId)
      this.grants.add(scope.threadId, scope.threadTitle, grantFolder);
  }

  /** `folder` as the person writes it: under their home folder, as `~/…`. */
  displayPath(folder: string): string {
    const home = this.options.env().HOME || homedir();
    return folder.startsWith(`${home}/`)
      ? `~${folder.slice(home.length)}`
      : folder;
  }

  /**
   * The git checkout holding `folder`, which a thread grant covers whole; `folder`
   * itself outside one. The search stops at `home`, so a checkout of the home folder
   * itself, such as a dotfiles repository, never widens a grant to all of it.
   */
  private async checkoutOf(folder: string, home: string): Promise<string> {
    for (
      let dir = folder;
      dir !== home && dir !== dirname(dir);
      dir = dirname(dir)
    ) {
      // `.git` is a folder in a clone and a file in a worktree.
      if (
        await stat(join(dir, ".git")).then(
          () => true,
          () => false,
        )
      )
        return dir;
    }
    return folder;
  }

  /** `~` and `~/…` are the person's home folder; anything else must be absolute. */
  private local(path: string): string {
    if (path === "~" || path.startsWith("~/")) {
      const home = this.options.env().HOME || homedir();
      return join(home, path.slice(2));
    }
    if (!isAbsolute(path))
      throw new Error("A local path must be absolute or start with ~/");
    return path;
  }

  private async target(path: string, write: boolean): Promise<string> {
    const local = this.local(path);
    try {
      return await realpath(local);
    } catch (error) {
      if (
        !write ||
        !(error instanceof Error) ||
        !("code" in error) ||
        error.code !== "ENOENT"
      )
        throw error;
      return join(await realpath(dirname(local)), basename(local));
    }
  }

  private async perform(
    params: Record<string, unknown>,
    bridge: Bridge | null,
  ): Promise<JsonObject> {
    if (this.closed || !bridge?.running || this.bridge !== bridge)
      throw new Error("Local access is offline");
    const expiresAt = params.expires_at;
    if (
      typeof expiresAt !== "number" ||
      !Number.isFinite(expiresAt) ||
      Date.now() / 1000 >= expiresAt
    )
      throw new Error("Local action expired");
    if (params.operation === "execute")
      return this.execute(params, expiresAt, bridge);
    if (
      params.operation === "read" ||
      params.operation === "write" ||
      params.operation === "import"
    )
      return this.file(params.operation, params, expiresAt, bridge);
    throw new Error("Unsupported local operation");
  }

  private async execute(
    params: Record<string, unknown>,
    expiresAt: number,
    bridge: Bridge,
  ): Promise<JsonObject> {
    const command = this.text(params, "command");
    if (DialogText.shown(command).length > COMMAND_LIMIT)
      throw new Error(
        "Local command is too long to review; write it to a local file and run that",
      );
    const requestedCwd = this.local(this.text(params, "cwd"));
    const cwd = await realpath(requestedCwd);
    if (!(await stat(cwd)).isDirectory())
      throw new Error("Working directory is not a directory");
    const timeout = params.timeout;
    if (
      typeof timeout !== "number" ||
      !Number.isInteger(timeout) ||
      timeout < 1 ||
      timeout > 300
    )
      throw new Error("Invalid timeout");
    await this.approve(
      "execute",
      `Run this exact command locally?\n\nWorking directory: ${DialogText.shown(cwd)}\nTimeout: ${timeout}s\n\n${DialogText.shown(command)}\n\nThis command has your full user and machine authority, including your files, credentials and network. The working directory is NOT a security sandbox.`,
      cwd,
      null,
      this.scope(params),
      expiresAt,
      bridge,
    );
    if ((await realpath(requestedCwd)) !== cwd)
      throw new Error("Working directory changed; request approval again");
    const executor = new LocalExecutor(cwd, { env: this.options.env() });
    this.executors.add(executor);
    try {
      return { ...(await executor.execute(command, timeout)) };
    } finally {
      this.executors.delete(executor);
    }
  }

  private async file(
    operation: "read" | "write" | "import",
    params: Record<string, unknown>,
    expiresAt: number,
    bridge: Bridge,
  ): Promise<JsonObject> {
    const write = operation === "write";
    const requested = this.text(params, "path");
    const target = await this.target(requested, write);
    const content = write ? this.text(params, "content") : "";
    const limit = operation === "import" ? IMPORT_LIMIT : READ_LIMIT;
    const size = write ? 0 : (await stat(target)).size;
    if (!write && size > limit)
      throw new Error(
        `Local file is ${size} bytes, over the ${operation === "import" ? "10 MiB import" : "64 KiB read"} limit`,
      );
    const action = {
      read: `Read this exact local file?\n\nRequested: ${DialogText.shown(requested)}\nResolved target: ${DialogText.shown(target)}\n\nIts contents will be sent to the cloud agent (maximum 64 KiB).`,
      write: `Write this exact local file?\n\nRequested: ${DialogText.shown(requested)}\nResolved target: ${DialogText.shown(target)}\n\nReplace its contents with:\n${DialogText.preview(content)}`,
      import: `Copy this exact local file into the cloud sandbox?\n\nRequested: ${DialogText.shown(requested)}\nResolved target: ${DialogText.shown(target)}\nSize: ${size} bytes\n\nA copy of the whole file will be uploaded to the cloud agent's sandbox (maximum 10 MiB).`,
    }[operation];
    await this.approve(
      operation,
      action,
      dirname(target),
      target,
      this.scope(params),
      expiresAt,
      bridge,
    );
    if ((await this.target(requested, write)) !== target)
      throw new Error("Local path changed; request approval again");
    const handle = await open(
      target,
      constants.O_NONBLOCK |
        constants.O_NOFOLLOW |
        (write ? constants.O_WRONLY | constants.O_CREAT : constants.O_RDONLY),
      0o600,
    );
    try {
      const info = await handle.stat();
      if (!info.isFile())
        throw new Error("Local access supports regular files only");
      if (write) {
        await handle.truncate(0);
        await handle.writeFile(content, "utf8");
        return { path: target, written: true };
      }
      // Bounded by the limit even if the file grew after approval.
      const buffer = Buffer.alloc(limit + 1);
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
      if (bytesRead > limit)
        throw new Error(`Local file grew past the ${limit}-byte limit`);
      const bytes = buffer.subarray(0, bytesRead);
      if (operation === "import")
        return {
          path: target,
          size: bytesRead,
          content_base64: bytes.toString("base64"),
        };
      return {
        path: target,
        content: new TextDecoder("utf-8", { fatal: true }).decode(bytes),
      };
    } finally {
      await handle.close();
    }
  }

  async close(): Promise<void> {
    this.closed = true;
    this.grants.clear();
    this.shutdown.abort();
    for (const executor of this.executors) executor.stopAll();
    await this.bridge?.close();
    this.bridge = null;
  }
}

module.exports = { DialogText, LocalAccessBridge };
