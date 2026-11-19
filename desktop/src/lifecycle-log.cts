/**
 * A small on-disk record of why the app started and stopped. The console
 * buffers behind "Save Diagnostics Report…" die with the process, so a build
 * that quits moments after launch leaves nothing to send; this file survives
 * and says which quit path ran. It holds lifecycle facts only, never page
 * content.
 */

import fs from "node:fs";
import type { RelaunchOptions } from "electron";

type Detail = Record<string, unknown>;

/** The part of Electron's `app` that starts and reports a quit. */
type QuittableApp = {
  quit: () => void;
  exit: (code?: number) => void;
  relaunch: (options?: RelaunchOptions) => void;
  on: (
    event: "before-quit" | "will-quit" | "quit",
    listener: (event: unknown, exitCode?: number) => void,
  ) => unknown;
};

export class LifecycleLog {
  constructor(
    private readonly filePath: string,
    private readonly maxBytes = 256 * 1024,
  ) {
    this.trim();
  }

  /** Synchronous so the line lands even when the process exits right after. */
  record(event: string, detail: Detail = {}): void {
    const suffix = LifecycleLog.formatDetail(detail);
    const line = `${new Date().toISOString()} pid=${process.pid} ${event}${suffix ? ` ${suffix}` : ""}\n`;
    try {
      fs.appendFileSync(this.filePath, line);
      this.trim();
    } catch {
      // Diagnostics must never take the app down.
    }
  }

  read(): string | null {
    try {
      return fs.readFileSync(this.filePath, "utf8");
    } catch {
      return null;
    }
  }

  /** Keep the newest half once the file outgrows its limit. */
  private trim(): void {
    try {
      if (fs.statSync(this.filePath).size <= this.maxBytes) return;
      const text = fs.readFileSync(this.filePath);
      const tail = text.subarray(-Math.floor(this.maxBytes / 2));
      fs.writeFileSync(this.filePath, tail.subarray(tail.indexOf(10) + 1));
    } catch {
      // No file yet, or it is unreadable; record() will create or skip it.
    }
  }

  /**
   * Record every quit, and whether the app itself asked for it. A `before-quit`
   * with `requestedByApp=false` came from outside this code: the Quit menu
   * role, the Dock, another process's quit Apple Event, or logout.
   */
  watchQuits(app: QuittableApp): void {
    let requestedByApp = false;
    const quit = app.quit.bind(app);
    const exit = app.exit.bind(app);
    const relaunch = app.relaunch.bind(app);
    app.quit = () => {
      requestedByApp = true;
      this.record("app.quit", { stack: LifecycleLog.callerStack() });
      quit();
    };
    app.exit = (code) => {
      requestedByApp = true;
      this.record("app.exit", {
        code: code ?? 0,
        stack: LifecycleLog.callerStack(),
      });
      if (code === undefined) exit();
      else exit(code);
    };
    app.relaunch = (options) => {
      this.record("app.relaunch", { stack: LifecycleLog.callerStack() });
      if (options === undefined) relaunch();
      else relaunch(options);
    };
    app.on("before-quit", () => {
      this.record("before-quit", { requestedByApp });
    });
    app.on("will-quit", () => this.record("will-quit"));
    app.on("quit", (_event, exitCode) => this.record("quit", { exitCode }));
  }

  private static formatDetail(detail: Detail): string {
    return Object.entries(detail)
      .filter(([, value]) => value !== undefined)
      .map(([key, value]) => {
        const text =
          value instanceof Error
            ? (value.stack ?? value.message)
            : typeof value === "string"
              ? value
              : JSON.stringify(value);
        return `${key}=${text.replace(/\s*\n\s*/g, " | ")}`;
      })
      .join(" ");
  }

  /** The call site of an app-initiated quit, without this module's own frames. */
  private static callerStack(): string {
    return (new Error().stack ?? "")
      .split("\n")
      .slice(1)
      .filter((line) => !/lifecycle-log\.c[jt]s/.test(line))
      .slice(0, 8)
      .map((line) => line.trim())
      .join(" | ");
  }
}
