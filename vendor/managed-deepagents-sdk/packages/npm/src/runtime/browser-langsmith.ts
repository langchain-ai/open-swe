/**
 * Browser use, the LangSmith side: create a key's browser, put Stagehand's
 * extension on it, and connect Stagehand to one of its windows.
 *
 * The LangSmith half of Python's `browser.py`, kept apart from the middleware
 * (`browser.ts`) so its tests can stand in for it, as Python's do for `_open`
 * and `_share_localhost`.
 */

import { createHash } from "node:crypto";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

import {
  getExtensionArchive,
  localBrowser,
  Stagehand,
  type BrowserContext,
  type Page,
} from "@browserbasehq/stagehand";
import type { SandboxBackendProtocolV2 } from "deepagents";
import { SandboxClient, type Sandbox } from "langsmith/sandbox";
import { z } from "zod";

import { StagehandFacadeTools } from "./browser-facade.js";
import {
  managedDeploymentName,
  managedSandboxName,
} from "./sandbox-manager.js";

/** The client and the browser's extension must match; `mda` pins this in deployments. */
export const STAGEHAND_VERSION = "4.3.0";

const BROWSER_GENERATIONS = 5;
const UNUSABLE = new Set(["deleting", "deleted", "failed", "error"]); // browser states to move past
const EXTENSION_DIR = `/var/lib/browserd/extensions/stagehand-${STAGEHAND_VERSION}`;
const EXTENSION_ZIP = "/tmp/mda-stagehand.zip"; // in the browser sandbox
const WINDOWS_FILE = "/var/lib/browserd/attachments.json"; // browserd's windows and their ports
const LOCALHOST_WINDOW = "mda-localhost"; // shares a sandbox's localhost with every window
export const HANDOFF_LINK_TTL_S = 900; // how long the link a user gets for a handoff works

export interface Session {
  readonly window: string;
  readonly browser: string; // the browser's name, which a later generation may have replaced
  readonly stagehand: Stagehand;
  readonly tools: StagehandFacadeTools;
  readonly artifacts: string; // where the facade writes run code's screenshots
  readonly transport: { close(): void } | undefined; // stagehand.rpcClient?.cdp, captured at connect
  closing: boolean; // run code called `browser.close()`: released after the call
  // The sandbox whose localhost the browser shares. By handle, not name: one MDA
  // replaces mid-run keeps its name but gets a new handle.
  shared: SandboxBackendProtocolV2 | undefined;
}

export type BrowsersApi = ReturnType<typeof browsersApi>;

interface BrowserWindow {
  id: string;
  name: string;
  api_cdp_url: string;
  localhost_alias?: string;
  controller?: "agent" | "user";
}

/**
 * How a handoff ended: `handed_back` when the user hands the window back;
 * `ended` when the handoff's link ran out, or its browser or window is gone.
 */
export type BrowserHandoffStatus = "handed_back" | "ended";

/** A handoff that waits on the user, in the thread's state. */
export const PendingBrowserHandoffSchema = z.object({
  /** The LangSmith browser's name. */
  browser: z.string(),
  /** The window the user has. */
  window_id: z.string(),
  /** When the user's link runs out, in epoch seconds. */
  until: z.number(),
});
export type PendingBrowserHandoff = z.infer<typeof PendingBrowserHandoffSchema>;

type BrowserInfo = { attachments?: BrowserWindow[] | null };

export async function closeSession(session: Session): Promise<void> {
  // Not `browser.close()`, which closes the browser. Closing Stagehand frees the
  // extension; dropping the CDP connection lets the browser idle out.
  await Promise.race([
    session.stagehand.close(),
    sleep(3_000, undefined, { ref: false }),
  ]).catch(() => undefined);
  try {
    session.transport?.close();
  } catch {}
  // As Python's rmtree(ignore_errors=True): closing never fails.
  await rm(session.artifacts, { recursive: true, force: true }).catch(
    () => undefined
  );
}

/**
 * `key`'s browser, under the deployment's prefix like its sandboxes.
 *
 * A later `generation` replaces a browser that is being deleted or broke.
 */
export function browserName(key: string, generation = 0): string {
  const scope =
    generation === 0 ? `browser:${key}` : `browser:${key}:${generation}`;
  const deployment = managedDeploymentName();
  if (deployment) return managedSandboxName(deployment, scope);
  return `mda-${createHash("sha256").update(scope).digest("hex").slice(0, 16)}`;
}

/**
 * `key`'s browser, created or started as needed, and its sandbox.
 *
 * A browser that is being deleted, or broke, can't be used or recreated under
 * its name, so `key` moves on to its next generation.
 */
export async function usableBrowser(
  api: BrowsersApi,
  boxes: Pick<SandboxClient, "getSandbox" | "startSandbox">,
  key: string,
  idleTtlSeconds: number
): Promise<{ name: string; box: Sandbox }> {
  for (let generation = 0; generation < BROWSER_GENERATIONS; generation++) {
    const name = browserName(key, generation);
    try {
      // HiDPI renders at twice the resolution, so a person watching or driving
      // the window sees it sharp; it can only be chosen at creation.
      const create = { name, idle_ttl_seconds: idleTtlSeconds, hidpi: true };
      await request(api, "POST", `${api.url}/browsers`, create);
    } catch (err) {
      if ((err as { status?: unknown }).status !== 409) throw err; // conflict: it exists
    }
    let box = await boxes.getSandbox(name);
    if (UNUSABLE.has(box.status ?? "")) continue;
    if (box.status === "stopped") {
      box = await boxes.startSandbox(name, { timeout: 300 });
    }
    return { name, box };
  }
  throw new Error("could not find or create a usable browser");
}

/** LangSmith's sandboxes API, and its headers: the key, and the workspace if set. */
export function browsersApi() {
  const workspace = process.env.LANGSMITH_WORKSPACE_ID;
  const key =
    process.env.LANGSMITH_API_KEY || process.env.LANGCHAIN_API_KEY || "";
  return {
    url: `${endpoint()}/v2/sandboxes`,
    headers: {
      "X-Api-Key": key,
      ...(workspace ? { "X-Tenant-Id": workspace } : {}),
    },
  };
}

/** LangSmith's API. */
export function endpoint(): string {
  const url =
    process.env.LANGSMITH_ENDPOINT ||
    process.env.LANGCHAIN_ENDPOINT ||
    "https://api.smith.langchain.com";
  return url.replace(/\/+$/, "");
}

/**
 * Attach Stagehand to a window of `key`'s browser, creating either as needed.
 *
 * The client reaches Chrome through the API's CDP proxy; the extension, inside
 * the browser sandbox, dials the window's local port. A window the user still
 * holds from an ended handoff is taken back.
 */
export async function openSession(
  key: string,
  windowName: string,
  idleTtlSeconds: number,
  onCloseRequested: () => Promise<void>
): Promise<Session> {
  const api = browsersApi();
  const { headers } = api;
  const browsers = `${api.url}/browsers`;
  const boxes = new SandboxClient({ apiKey: headers["X-Api-Key"], headers });

  const { name, box } = await usableBrowser(api, boxes, key, idleTtlSeconds);
  await installExtension(box);
  await request(api, "POST", `${browsers}/${name}/extensions/load`); // a no-op once loaded
  const window = await findWindow(api, `${browsers}/${name}`, windowName);
  if (window.controller === "user") {
    const controller = `${browsers}/${name}/attachments/${window.id}/controller`;
    await request(api, "POST", controller, { controller: "agent" });
  }
  const debugging = `${window.api_cdp_url}/json/version`;
  const cdpUrl = (
    await request<{ webSocketDebuggerUrl: string }>(api, "GET", debugging)
  ).webSocketDebuggerUrl;
  const port = await windowPort(box, window.id);

  const localUrl = `ws://127.0.0.1:${port}/devtools/browser/${window.id}`;
  const attach = () =>
    connect(windowName, name, cdpUrl, localUrl, headers, onCloseRequested);
  try {
    return await attach();
  } catch (err) {
    if (!String(err).includes("already initialized")) throw err;
  }
  // A client that never closed (a crash, another replica) left its claim in the
  // extension; reinstalling it clears the claim without restarting Chrome.
  await reinstallExtension(cdpUrl, headers);
  return attach();
}

/** The browser's window `name`, created for the agent if it has none. */
async function findWindow(
  api: BrowsersApi,
  browser: string,
  name: string
): Promise<BrowserWindow> {
  const info = await request<BrowserInfo>(api, "GET", browser);
  const window = info.attachments?.find((w) => w.name === name);
  if (window) return window;
  const body = { name, controller: "agent" };
  return request<BrowserWindow>(api, "POST", `${browser}/attachments`, body);
}

/**
 * Give the user `key`'s window, creating either as needed.
 *
 * Returns the browser's name, the window's id, and a short-lived link the user
 * drives it with.
 */
export async function handOver(
  key: string,
  windowName: string,
  idleTtlSeconds: number
): Promise<{ browser: string; windowId: string; url: string }> {
  const api = browsersApi();
  const { headers } = api;
  const browsers = `${api.url}/browsers`;
  const boxes = new SandboxClient({ apiKey: headers["X-Api-Key"], headers });
  const { name } = await usableBrowser(api, boxes, key, idleTtlSeconds);
  const window = await findWindow(api, `${browsers}/${name}`, windowName);
  const windowApi = `${browsers}/${name}/attachments/${window.id}`;
  await request(api, "POST", `${windowApi}/controller`, { controller: "user" });
  const body = {
    ttl_seconds: HANDOFF_LINK_TTL_S,
    embed_origins: await embedOrigins(api),
  };
  const link = await request<{ act_url: string }>(
    api,
    "POST",
    `${windowApi}/links`,
    body
  );
  return { browser: name, windowId: window.id, url: link.act_url };
}

/** How the handoff ended, or `undefined` while the user still has the window. */
export async function handoffStatus(
  pending: PendingBrowserHandoff
): Promise<BrowserHandoffStatus | undefined> {
  const api = browsersApi();
  try {
    const browser = `${api.url}/browsers/${pending.browser}`;
    const info = await request<BrowserInfo>(
      api,
      "GET",
      browser,
      undefined,
      10_000
    );
    const window = info.attachments?.find((w) => w.id === pending.window_id);
    if (window === undefined) return "ended";
    if (window.controller === "agent") return "handed_back";
  } catch (err) {
    if ((err as { status?: unknown }).status === 404) return "ended";
    // Asked again later.
  }
  return Date.now() / 1000 >= pending.until ? "ended" : undefined;
}

/**
 * Origins whose pages may frame a handoff's window: the LangSmith UI, plus any
 * in `MDA_BROWSER_EMBED_ORIGINS` (comma-separated), such as a UI preview build.
 */
export async function embedOrigins(api: BrowsersApi): Promise<string[]> {
  const extra = (process.env.MDA_BROWSER_EMBED_ORIGINS ?? "").split(",");
  const ui = await langsmithUiOrigin(api);
  const origins = [ui, ...extra.map((o) => o.trim()).filter(Boolean)];
  return [...new Set(origins)];
}

/**
 * The LangSmith UI's origin, where Studio may embed a handoff's window: the
 * workspace's `dashboard_url`.
 */
async function langsmithUiOrigin(api: BrowsersApi): Promise<string> {
  const workspace = `${endpoint()}/v1/platform/workspaces/current/info`;
  const info = await request<{ dashboard_url: string }>(api, "GET", workspace);
  return new URL(info.dashboard_url).origin;
}

/**
 * Let browser `name` open `sandbox`'s servers at `http://localhost:<port>`.
 *
 * One window attached with `share_localhost` serves the whole browser. A
 * sandbox MDA replaced under the same name keeps the window's `sandbox` but not
 * its id, so the window is checked by the id its `localhost_alias` names.
 */
export async function shareLocalhost(
  name: string,
  sandbox: string
): Promise<void> {
  const api = browsersApi();
  const browser = `${api.url}/browsers/${name}`;
  const info = await request<BrowserInfo>(api, "GET", browser);
  const window = info.attachments?.find((w) => w.name === LOCALHOST_WINDOW);
  if (window) {
    const box = `${api.url}/boxes/${sandbox}`;
    const { id } = await request<{ id: string }>(api, "GET", box);
    if (window.localhost_alias === localhostAlias(id)) return;
    await request(api, "DELETE", `${browser}/attachments/${window.id}`); // an old sandbox's
  }
  const body = { name: LOCALHOST_WINDOW, sandbox, share_localhost: true };
  await request(api, "POST", `${browser}/attachments`, body);
}

/** The browser's host for a sandbox's localhost: `sb-` and its id in base32. */
export function localhostAlias(sandboxId: string): string {
  const hex = sandboxId.replaceAll("-", "");
  if (!/^[\da-f]{32}$/i.test(hex)) {
    throw new Error("badly formed hexadecimal UUID string"); // as uuid.UUID
  }
  // RFC 4648 base32 of the UUID's 16 bytes, lowercase and unpadded: 26 digits.
  const alphabet = "abcdefghijklmnopqrstuvwxyz234567";
  const digits = (BigInt(`0x${hex}`) << 2n).toString(32).padStart(26, "0");
  const label = [...digits].map((d) => alphabet[parseInt(d, 32)]).join("");
  return `sb-${label}.localhost`;
}

/** A browsers API call's JSON, if any; an unsuccessful status throws, as Python's `_raise` does. */
async function request<T>(
  api: BrowsersApi,
  method: string,
  url: string,
  body?: object,
  timeoutMs = 90_000
): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: body
      ? { ...api.headers, "Content-Type": "application/json" }
      : api.headers,
    body: body && JSON.stringify(body),
    redirect: "manual", // as httpx: a redirect is an error, and the key never follows one
    signal: AbortSignal.timeout(timeoutMs),
  });
  const text = await response.text(); // read even when unused, freeing the connection
  if (!response.ok) {
    const msg = `LangSmith browsers API: HTTP ${response.status} ${text.slice(0, 300)}`;
    throw Object.assign(new Error(msg), { status: response.status });
  }
  return (text ? JSON.parse(text) : undefined) as T;
}

export async function connect(
  window: string,
  browser: string,
  cdpUrl: string,
  localUrl: string,
  headers: Record<string, string>,
  onCloseRequested: () => Promise<void>
): Promise<Session> {
  const handle = await localBrowser.connect({
    cdpUrl,
    extensionCdpUrl: localUrl,
    headers,
  });
  // When this fails, 4.3.0 has no public way to drop `handle`: a known leak.
  const stagehand = await Stagehand.create({ browser: handle });
  const artifacts = await mkdtemp(join(tmpdir(), "mda-browser-"));
  // Run code's `browser.close()` releases only our client, not the browser.
  const tools = new StagehandFacadeTools(stagehand, {
    artifactRoot: artifacts,
    onCloseRequested,
    quietWait: true,
    keeperPage: false,
  });
  // The facade acts on Chrome's focused tab, which is often in a window this client
  // cannot see (another thread's, or the shared localhost one), and `setActivePage`
  // does not move headless Chrome's focus. It has no hook for the page, so name ours.
  Object.assign(tools, { activePage: () => newestTab(handle.context) });
  return {
    window,
    browser,
    stagehand,
    tools,
    artifacts,
    transport: stagehand.rpcClient?.cdp, // `stagehand.close()` clears `rpcClient`
    closing: false,
    shared: undefined,
  };
}

/** The window's newest tab, as it was: the client's CDP lists only its window's. */
async function newestTab(context: BrowserContext): Promise<Page> {
  const pages = await context.pages();
  return pages.at(-1) ?? (await context.newPage());
}

/** Unpack the client's own extension into the browser, unless it is there already. */
export async function installExtension(box: Sandbox): Promise<void> {
  const check = `test -f ${EXTENSION_DIR}/manifest.json`;
  if ((await box.run(check, { timeout: 60 })).exit_code === 0) return;
  await box.write(EXTENSION_ZIP, await getExtensionArchive(), 300);
  const tmp = `${EXTENSION_DIR}.tmp`; // unpacked aside, so a partial copy never passes the check
  const unpacked = await box.run(
    `rm -rf ${tmp} && python3 -m zipfile -e ${EXTENSION_ZIP} ${tmp} && ` +
      `rm -rf ${EXTENSION_DIR} && mv ${tmp} ${EXTENSION_DIR}`,
    { timeout: 300 }
  );
  if (unpacked.exit_code !== 0) {
    const msg = `could not install Stagehand in the browser: ${unpacked.stderr.slice(-500)}`;
    throw new Error(msg);
  }
}

/** Uninstall Stagehand's extension and load it again over CDP. */
async function reinstallExtension(
  cdpUrl: string,
  headers: Record<string, string>
): Promise<void> {
  const ws = new WebSocket(cdpUrl, { headers });
  let fail: (reason: unknown) => void = () => undefined;
  const failed = new Promise<never>((_, reject) => {
    fail = reject;
  });
  failed.catch(() => undefined); // raced by each step; it may also settle after the last
  const deadline = AbortSignal.timeout(30_000);
  deadline.addEventListener("abort", () => fail(deadline.reason));
  ws.addEventListener("close", () => fail(new Error("CDP connection closed")));
  type Reply = { result?: Record<string, unknown>; error?: unknown };
  const waiting = new Map<number, (reply: Reply) => void>();
  ws.addEventListener("message", (event) => {
    try {
      const reply = JSON.parse(String(event.data));
      waiting.get(reply.id ?? -1)?.(reply); // events share the socket
    } catch (err) {
      fail(err); // a listener that throws would crash the process
    }
  });

  async function cdp(id: number, method: string, params: object = {}) {
    const reply = new Promise<Reply>((resolve) => waiting.set(id, resolve));
    ws.send(JSON.stringify({ id, method, params }));
    const { error, result } = await Promise.race([reply, failed]);
    if (error !== undefined) {
      throw new Error(`CDP ${method} failed: ${JSON.stringify(error)}`);
    }
    return result ?? {};
  }

  try {
    const open = new Promise((resolve) => ws.addEventListener("open", resolve));
    await Promise.race([open, failed]);
    const { extensions } = (await cdp(0, "Extensions.getExtensions")) as {
      extensions: { id: string; name: string }[];
    };
    for (const extension of extensions) {
      if (extension.name === "Stagehand Runtime") {
        await cdp(1, "Extensions.uninstall", { id: extension.id });
      }
    }
    await cdp(2, "Extensions.loadUnpacked", { path: EXTENSION_DIR });
  } finally {
    ws.close();
  }
}

/** The port browserd serves a window on inside the browser sandbox, once it is saved. */
async function windowPort(box: Sandbox, windowId: string): Promise<number> {
  for (let attempt = 0; attempt < 20; attempt++) {
    const saved = await box.run(`cat ${WINDOWS_FILE}`, { timeout: 60 });
    try {
      const { attachments } = JSON.parse(saved.stdout) as {
        attachments: { id: unknown; port: unknown }[];
      };
      const port = attachments.find((w) => w.id === windowId)?.port;
      if (port) return Number(port);
    } catch {} // not saved yet
    await sleep(250);
  }
  throw new Error("could not find the browser window's port");
}
