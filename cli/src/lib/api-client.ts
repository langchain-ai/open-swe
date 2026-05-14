import {
  ApiError,
  TokenExpiredError,
  type AuthStartResponse,
  type CliServerConfig,
  type CreateRunBody,
  type CreateRunResponse,
  type InterruptResponse,
  type MeResponse,
  type RunSummary,
  type SendMessageResponse,
  type ApiErrorBody,
} from '@lib/api-types';

// Updated by build; falls back to "dev" if not set.
const CLI_VERSION = process.env.OPENSWE_CLI_VERSION ?? '0.1.0';
const USER_AGENT = `open-swe-cli/${CLI_VERSION}`;

type RequestOptions = {
  method?: string;
  body?: unknown;
  authenticated?: boolean;
  signal?: AbortSignal;
};

export class ApiClient {
  readonly backend_url: string;
  private session_token?: string;

  constructor(backend_url: string, session_token?: string) {
    this.backend_url = backend_url.replace(/\/+$/, '');
    this.session_token = session_token;
  }

  setSessionToken(token: string | undefined): void {
    this.session_token = token;
  }

  getSessionToken(): string | undefined {
    return this.session_token;
  }

  private buildUrl(pathname: string, query?: Record<string, string | undefined>): string {
    const url = new URL(this.backend_url + pathname);
    if (query) {
      for (const [k, v] of Object.entries(query)) {
        if (v !== undefined) url.searchParams.set(k, v);
      }
    }
    return url.toString();
  }

  private async request<T>(pathname: string, opts: RequestOptions = {}): Promise<T> {
    const headers: Record<string, string> = {
      'User-Agent': USER_AGENT,
      Accept: 'application/json',
    };
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    if (opts.authenticated !== false && this.session_token) {
      headers.Authorization = `Bearer ${this.session_token}`;
    }
    const res = await fetch(this.buildUrl(pathname), {
      method: opts.method ?? 'GET',
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: opts.signal,
    });
    if (!res.ok) {
      let body: ApiErrorBody = {};
      try {
        body = (await res.json()) as ApiErrorBody;
      } catch {
        // ignore parse errors
      }
      const code = body.code ?? `http_${res.status}`;
      const message = body.message ?? body.detail ?? res.statusText ?? 'Request failed';
      if (res.status === 401 && code === 'token_expired') {
        throw new TokenExpiredError(message);
      }
      throw new ApiError(res.status, code, message);
    }
    if (res.status === 204) return undefined as unknown as T;
    const ct = res.headers.get('content-type') ?? '';
    if (!ct.includes('application/json')) {
      return undefined as unknown as T;
    }
    return (await res.json()) as T;
  }

  getConfig(): Promise<CliServerConfig> {
    return this.request<CliServerConfig>('/cli/config', { authenticated: false });
  }

  startAuth(redirect_uri: string, state: string): Promise<AuthStartResponse> {
    const url = `/cli/auth/start?redirect_uri=${encodeURIComponent(redirect_uri)}&state=${encodeURIComponent(state)}`;
    return this.request<AuthStartResponse>(url, { authenticated: false });
  }

  me(): Promise<MeResponse> {
    return this.request<MeResponse>('/cli/me');
  }

  listRuns(): Promise<RunSummary[]> {
    return this.request<RunSummary[]>('/cli/runs');
  }

  createRun(body: CreateRunBody): Promise<CreateRunResponse> {
    return this.request<CreateRunResponse>('/cli/runs', { method: 'POST', body });
  }

  sendMessage(thread_id: string, content: string): Promise<SendMessageResponse> {
    return this.request<SendMessageResponse>(
      `/cli/runs/${encodeURIComponent(thread_id)}/messages`,
      { method: 'POST', body: { content } },
    );
  }

  interrupt(thread_id: string): Promise<InterruptResponse> {
    return this.request<InterruptResponse>(
      `/cli/runs/${encodeURIComponent(thread_id)}/interrupt`,
      { method: 'POST' },
    );
  }

  streamUrl(thread_id: string, since?: string): string {
    return this.buildUrl(`/cli/runs/${encodeURIComponent(thread_id)}/stream`, { since });
  }

  authHeaders(): Record<string, string> {
    const h: Record<string, string> = { 'User-Agent': USER_AGENT };
    if (this.session_token) h.Authorization = `Bearer ${this.session_token}`;
    return h;
  }
}
