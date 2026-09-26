export {};

declare global {
  /** render()'s 3rd arg, an open/extensible dict. v1 has only `mode`
   * ("dark"|"light"); the sandbox sets `html.dark` from it. */
  type RenderMetadata = { mode: 'dark' | 'light' };

  interface Window {
    /** Injected by the host page (LangSmith, or `langsmith apps dev` locally) — see AGENTS.md. */
    langsmith: {
      call: (
        operation: string,
        args?: { params?: Record<string, string | string[]>; body?: unknown }
      ) => Promise<unknown>;
      setData: (patch: Record<string, unknown>) => void;
      openUrl: (url: string, options?: { newTab?: boolean }) => void;
      feedback: {
        create: (args: Record<string, unknown>) => Promise<unknown>;
      };
    };
  }
}
