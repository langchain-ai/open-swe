export type DeploymentConfig = {
  backend_url: string;
  session_token: string;
  github_login: string;
};

export type CliConfig = {
  default?: string;
  deployments: Record<string, DeploymentConfig>;
};

export type CliServerConfig = {
  github_app_client_id: string;
  allowed_org: string;
  server_version: string;
  supports_handoff: boolean;
  cli_api_version: number;
};

export type AuthStartResponse = {
  authorize_url: string;
};

export type MeResponse = {
  github_login: string;
  email: string | null;
};

export type RunSource = 'github' | 'slack' | 'linear' | 'cli';
export type RunStatus = 'running' | 'idle' | 'completed' | 'error';

export type RunSummary = {
  thread_id: string;
  source: RunSource;
  title: string;
  status: RunStatus;
  last_event_at: string;
  repo: string | null;
  branch: string | null;
  source_url: string | null;
};

export type CreateRunBody = {
  repo: string;
  branch: string;
  prompt: string;
  model?: string;
  agent?: string;
};

export type CreateRunResponse = {
  thread_id: string;
};

export type SendMessageResponse = {
  queued_at: string;
};

export type InterruptResponse = {
  interrupted: boolean;
};

export type ApiErrorBody = {
  code?: string;
  message?: string;
  detail?: string;
};

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

export class TokenExpiredError extends ApiError {
  constructor(message = 'Session token expired') {
    super(401, 'token_expired', message);
    this.name = 'TokenExpiredError';
  }
}

export type SSEEvent = {
  event?: string;
  data: unknown;
  id?: string;
};
