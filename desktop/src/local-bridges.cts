import { basename } from "node:path";

import {
  Bridge,
  BridgeHttpApi,
  type BridgeSender,
} from "open-swe-bridge-client";

/**
 * The bridges that let the cloud agent run a "This Mac" thread's commands in
 * its checkout here. One per thread, opened when the thread is started or next
 * used from this app and served until the app quits.
 *
 * The backend's thread metadata holds the bridge id forever, so a thread is
 * always served through the bridge it was created with: reopening one the
 * backend closed (the Mac slept, the app quit) keeps its id.
 */
interface LocalBridgesOptions {
  send: BridgeSender;
  /** The checkout the thread works in right now, or null if it is gone. */
  rootFor: (threadId: string) => string | null;
  bridgeIdFor: (threadId: string) => string | null;
  remember: (threadId: string, bridgeId: string) => void;
  /** The environment commands start from: the user's login shell's. */
  env: () => Record<string, string | undefined>;
  log: (message: string, error?: unknown) => void;
}

const CREDENTIAL_REJECTED =
  "The Open SWE session expired. Sign in again to keep running local threads.";

class LocalBridges {
  private readonly api: BridgeHttpApi;
  private readonly bridges = new Map<string, Bridge>();
  private readonly opening = new Map<string, Promise<Bridge>>();

  constructor(private readonly options: LocalBridgesOptions) {
    this.api = new BridgeHttpApi(options.send);
  }

  /** Whether this app is serving the thread's bridge right now. */
  serving(threadId: string): boolean {
    return this.bridges.get(threadId)?.running === true;
  }

  /** Serve the thread's checkout, opening or reopening its bridge as needed. */
  async ensure(threadId: string): Promise<string> {
    const live = this.bridges.get(threadId);
    if (live?.running) return live.session.bridgeId;
    let pending = this.opening.get(threadId);
    if (!pending) {
      pending = this.open(threadId).finally(() =>
        this.opening.delete(threadId),
      );
      this.opening.set(threadId, pending);
    }
    return (await pending).session.bridgeId;
  }

  private async open(threadId: string): Promise<Bridge> {
    const root = this.options.rootFor(threadId);
    if (!root)
      throw new Error("This thread's checkout is no longer on This Mac");
    const bridge = await Bridge.open(this.api, {
      client: "desktop",
      rootPath: () => this.options.rootFor(threadId) ?? root,
      label: basename(root),
      bridgeId: this.options.bridgeIdFor(threadId),
      credentialRejected: CREDENTIAL_REJECTED,
      log: (message) =>
        this.options.log(`Local bridge for ${threadId}: ${message}`),
      env: this.options.env(),
    });
    this.options.remember(threadId, bridge.session.bridgeId);
    this.bridges.set(threadId, bridge);
    bridge.start((error) => {
      // The next use reopens it; a run started meanwhile reports the Mac offline.
      this.options.log(`Local bridge for ${threadId} stopped`, error);
      if (this.bridges.get(threadId) === bridge) this.bridges.delete(threadId);
    });
    return bridge;
  }

  async close(threadId: string): Promise<void> {
    const bridge = this.bridges.get(threadId);
    this.bridges.delete(threadId);
    await bridge?.close();
  }

  async closeAll(): Promise<void> {
    const bridges = [...this.bridges.values()];
    this.bridges.clear();
    await Promise.allSettled(bridges.map((bridge) => bridge.close()));
  }
}

module.exports = { LocalBridges };
