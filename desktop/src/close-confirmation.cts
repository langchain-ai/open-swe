export class CloseConfirmation {
  private pending = false;

  constructor(
    private readonly app: Pick<Electron.App, "quit">,
    private readonly dialog: Pick<
      typeof import("electron").dialog,
      "showMessageBox"
    >,
    private readonly appName: string,
  ) {}

  async request(
    action: "quit" | "close",
    window: Electron.BrowserWindow | null,
  ) {
    if (this.pending || (action === "close" && !window)) return;
    this.pending = true;
    try {
      const options: Electron.MessageBoxOptions = {
        type: "question",
        message:
          action === "quit" ? `Quit ${this.appName}?` : "Close this window?",
        detail:
          action === "quit"
            ? "Quitting disconnects any running This Mac threads."
            : "You can reopen the window from the Dock.",
        buttons: ["Cancel", action === "quit" ? "Quit" : "Close Window"],
        defaultId: 0,
        cancelId: 0,
      };
      const result =
        window && !window.isDestroyed()
          ? await this.dialog.showMessageBox(window, options)
          : await this.dialog.showMessageBox(options);
      if (result.response !== 1) return;
      if (action === "quit") this.app.quit();
      else if (window && !window.isDestroyed()) window.close();
    } finally {
      this.pending = false;
    }
  }
}
