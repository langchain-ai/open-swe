import crypto from 'crypto';
import { ApiClient } from '@lib/api-client';
import type { CliServerConfig, DeploymentConfig } from '@lib/api-types';
import { CLIENT_CLI_API_VERSION } from '@lib/constants';

export function assertServerCompatible(config: CliServerConfig): void {
  if (config.cli_api_version > CLIENT_CLI_API_VERSION) {
    throw new Error(
      `This Open SWE deployment speaks cli_api_version=${config.cli_api_version}, ` +
        `but this CLI only understands ${CLIENT_CLI_API_VERSION}. ` +
        `Run \`openswe upgrade\` to get a newer client.`,
    );
  }
}

const CALLBACK_TIMEOUT_MS = 5 * 60 * 1000;

type CallbackPayload = {
  session_token: string;
  github_login: string;
  state: string;
};

function isCallbackPayload(v: unknown): v is CallbackPayload {
  if (typeof v !== 'object' || v === null) return false;
  const r = v as Record<string, unknown>;
  return (
    typeof r.session_token === 'string' &&
    typeof r.github_login === 'string' &&
    typeof r.state === 'string'
  );
}

export async function openBrowser(url: string): Promise<void> {
  let cmd: string;
  let args: string[];
  switch (process.platform) {
    case 'darwin':
      cmd = 'open';
      args = [url];
      break;
    case 'win32':
      cmd = 'cmd';
      args = ['/c', 'start', '""', url];
      break;
    default:
      cmd = 'xdg-open';
      args = [url];
      break;
  }
  try {
    // Prefer Bun.spawn when available so we don't pull in node:child_process types.
    const bunGlobal = (globalThis as unknown as { Bun?: { spawn: (a: string[], o?: unknown) => unknown } }).Bun;
    if (bunGlobal?.spawn) {
      bunGlobal.spawn([cmd, ...args], { stdout: 'ignore', stderr: 'ignore' });
      return;
    }
    const { spawn } = await import('child_process');
    const child = spawn(cmd, args, { stdio: 'ignore', detached: true });
    child.unref();
  } catch {
    // best-effort; caller prints URL anyway
  }
}

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (v: T) => void;
  reject: (err: unknown) => void;
};

function defer<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  let reject!: (err: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

type CallbackServer = {
  port: number;
  stop: () => void;
};

type BunServer = {
  port: number;
  stop: (closeActive?: boolean) => void;
};

type BunGlobal = {
  serve: (opts: {
    port: number;
    fetch: (req: Request) => Response | Promise<Response>;
  }) => BunServer;
};

type FetchHandler = (req: Request) => Response | Promise<Response>;

async function startCallbackServer(handler: FetchHandler): Promise<CallbackServer> {
  const bunGlobal = (globalThis as unknown as { Bun?: BunGlobal }).Bun;
  if (bunGlobal?.serve) {
    const server = bunGlobal.serve({ port: 0, fetch: handler });
    return { port: server.port, stop: () => server.stop(true) };
  }
  // Node fallback: works under `#!/usr/bin/env node`, which is how `bun link`
  // installs the binary on PATH. We adapt http.IncomingMessage <-> Request
  // because the rest of this module is written against the WHATWG types.
  const http = await import('node:http');
  return await new Promise<CallbackServer>((resolve, reject) => {
    const server = http.createServer((req, res) => {
      void (async () => {
        try {
          const chunks: Buffer[] = [];
          for await (const c of req as AsyncIterable<Buffer>) chunks.push(c);
          const body = Buffer.concat(chunks);
          const host = req.headers.host ?? '127.0.0.1';
          const url = `http://${host}${req.url ?? '/'}`;
          const init: RequestInit = {
            method: req.method,
            headers: req.headers as Record<string, string>,
          };
          if (body.length > 0 && req.method && req.method !== 'GET' && req.method !== 'HEAD') {
            init.body = body;
          }
          const request = new Request(url, init);
          const response = await handler(request);
          const headers: Record<string, string> = {};
          response.headers.forEach((v, k) => { headers[k] = v; });
          res.writeHead(response.status, headers);
          const text = await response.text();
          res.end(text);
        } catch (err) {
          res.writeHead(500);
          res.end(err instanceof Error ? err.message : String(err));
        }
      })();
    });
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const addr = server.address();
      const port = typeof addr === 'object' && addr ? addr.port : 0;
      resolve({
        port,
        stop: () => {
          try { server.close(); } catch { /* best effort */ }
        },
      });
    });
  });
}

export type LoginOptions = {
  onStatus?: (message: string) => void;
};

export async function login(
  backend_url: string,
  opts: LoginOptions = {},
): Promise<DeploymentConfig> {
  const status = opts.onStatus ?? (() => undefined);

  const api = new ApiClient(backend_url);
  // Surface backend reachability early; also confirms it's an Open SWE deployment.
  status('Fetching deployment config');
  const serverConfig = await api.getConfig();
  assertServerCompatible(serverConfig);

  const state = crypto.randomUUID();
  const result = defer<CallbackPayload>();

  const server = await startCallbackServer(async (req: Request) => {
      const url = new URL(req.url);
      if (req.method === 'OPTIONS') {
        return new Response(null, {
          status: 204,
          headers: corsHeaders(),
        });
      }
      if (url.pathname !== '/callback') {
        return new Response('Not found', { status: 404, headers: corsHeaders() });
      }
      if (req.method !== 'POST') {
        return new Response('Method not allowed', { status: 405, headers: corsHeaders() });
      }
      let body: unknown;
      try {
        body = await req.json();
      } catch {
        return new Response(JSON.stringify({ error: 'invalid_json' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json', ...corsHeaders() },
        });
      }
      if (!isCallbackPayload(body)) {
        return new Response(JSON.stringify({ error: 'invalid_payload' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json', ...corsHeaders() },
        });
      }
      if (body.state !== state) {
        result.reject(new Error('OAuth state mismatch'));
        return new Response(JSON.stringify({ error: 'state_mismatch' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json', ...corsHeaders() },
        });
      }
      result.resolve(body);
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json', ...corsHeaders() },
      });
  });

  const port = server.port;
  const redirectUri = `http://127.0.0.1:${port}/callback`;

  try {
    status('Starting OAuth handshake');
    const { authorize_url } = await api.startAuth(redirectUri, state);
    status('Opening browser');
    await openBrowser(authorize_url);
    status(`Waiting for callback (if browser did not open: ${authorize_url})`);

    const payload = await Promise.race([
      result.promise,
      new Promise<CallbackPayload>((_, reject) =>
        setTimeout(() => reject(new Error('Login timed out after 5 minutes')), CALLBACK_TIMEOUT_MS),
      ),
    ]);
    status('Verifying session');

    return {
      backend_url: api.backend_url,
      session_token: payload.session_token,
      github_login: payload.github_login,
    };
  } finally {
    try {
      server.stop();
    } catch {
      // ignore
    }
  }
}

function corsHeaders(): Record<string, string> {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  };
}
