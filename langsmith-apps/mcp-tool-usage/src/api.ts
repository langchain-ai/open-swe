import type {
  ChartPreviewResponse,
  Project,
  ThreadCandidate,
  ToolCallRun,
  ToolCallRunsResponse,
  TopTools,
  TrajectoryMessage,
  TrajectoryResponse,
  WindowKey,
} from './types';
import { parseToolName } from './lib/tools';

export const DEFAULT_PROJECT_NAME = 'open-swe-v3';

const MCP_TOOL_FILTER = 'and(eq(run_type, "tool"), eq(metadata_key, "mcp_tool_name"))';
const MAX_GROUPS = 20;
const SAMPLE_SIZE = 200;
const MAX_TRAJECTORY_PAGES = 10;

const WINDOW_DAYS: Record<WindowKey, number> = { '1d': 1, '7d': 7 };

type CallArgs = Parameters<Window['langsmith']['call']>[1];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// The bridge drops HTTP status codes, so a 429 is indistinguishable from any other failure.
async function call<T>(operation: string, args?: CallArgs): Promise<T> {
  for (let attempt = 1; ; attempt++) {
    try {
      return (await window.langsmith.call(operation, args)) as T;
    } catch (e) {
      if (attempt >= 4) throw e;
      await sleep(Math.min(500 * 2 ** (attempt - 1), 4000) + Math.random() * 300);
    }
  }
}

function windowBounds(window: WindowKey): { start: string; end: string; days: number } {
  const days = WINDOW_DAYS[window];
  const end = new Date();
  const start = new Date(end.getTime() - days * 86_400_000);
  return { start: start.toISOString(), end: end.toISOString(), days };
}

export async function fetchProjects(search = '', offset = 0, limit = 25): Promise<Project[]> {
  const params: Record<string, string> = {
    limit: String(limit),
    offset: String(offset),
    reference_free: 'true',
  };
  if (search) params.name_contains = search;
  return call<Project[]>('GET /api/v1/sessions', { params });
}

export async function findProjectByName(name: string): Promise<Project | null> {
  const projects = await call<Project[]>('GET /api/v1/sessions', { params: { name, limit: '1' } });
  return projects[0] ?? null;
}

export async function fetchTopTools(projectId: string, window: WindowKey): Promise<TopTools> {
  const { start, end, days } = windowBounds(window);
  const filters = { session: [projectId], filter: MCP_TOOL_FILTER };
  const response = await call<ChartPreviewResponse>('POST /api/v1/charts/preview', {
    body: {
      bucket_info: { start_time: start, end_time: end, stride: { days } },
      chart: {
        series: [
          {
            id: crypto.randomUUID(),
            name: 'by tool',
            metric: 'run_count',
            filters,
            group_by: { attribute: 'name', max_groups: MAX_GROUPS },
          },
          { id: 'total', name: 'total', metric: 'run_count', filters },
        ],
      },
    },
  });

  const counts = new Map<string, number>();
  let total = 0;
  for (const point of response.data) {
    const value = point.value ?? 0;
    if (point.series_id === 'total') {
      total += value;
    } else if (point.group) {
      counts.set(point.group, (counts.get(point.group) ?? 0) + value);
    }
  }
  const tools = [...counts.entries()]
    .map(([runName, count]) => ({ runName, count, ...parseToolName(runName) }))
    .sort((a, b) => b.count - a.count);
  return { tools, total };
}

// MCP failures come back as successful runs whose content reads "Error: …" or "Input validation error: …".
function looksLikeError(run: ToolCallRun): boolean {
  if (run.status === 'error') return true;
  return /^(tool:\s*)?[\w ]{0,30}\berror\b/i.test(run.outputs_preview?.trim() ?? '');
}

/** Threads that used the tool: error-free first, then by closeness of call count to the median. */
export async function fetchThreadCandidates(
  projectId: string,
  runName: string,
  window: WindowKey
): Promise<ThreadCandidate[]> {
  const { start } = windowBounds(window);
  const response = await call<ToolCallRunsResponse>('POST /api/v2/runs/query', {
    body: {
      project_ids: [projectId],
      filter: `eq(name, ${JSON.stringify(runName)})`,
      min_start_time: start,
      page_size: SAMPLE_SIZE,
      selects: ['ID', 'TRACE_ID', 'THREAD_ID', 'START_TIME', 'STATUS', 'OUTPUTS_PREVIEW'],
    },
  });

  const byThread = new Map<string, ToolCallRun[]>();
  for (const run of response.items) {
    if (!run.thread_id) continue;
    byThread.set(run.thread_id, [...(byThread.get(run.thread_id) ?? []), run]);
  }
  const candidates = [...byThread.entries()].map(([threadId, calls]) => ({
    threadId,
    calls,
    failed: calls.some(looksLikeError),
  }));
  if (candidates.length === 0) return [];

  const sizes = candidates.map((c) => c.calls.length).sort((a, b) => a - b);
  const median = sizes[Math.floor(sizes.length / 2)];
  return candidates
    .sort(
      (a, b) =>
        Number(a.failed) - Number(b.failed) ||
        Math.abs(a.calls.length - median) - Math.abs(b.calls.length - median) ||
        b.calls[0].start_time.localeCompare(a.calls[0].start_time)
    )
    .map(({ threadId, calls }) => ({ threadId, calls }));
}

export async function fetchTrajectory(projectId: string, threadId: string): Promise<TrajectoryMessage[]> {
  const messages: TrajectoryMessage[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < MAX_TRAJECTORY_PAGES; page++) {
    const response: TrajectoryResponse = await call<TrajectoryResponse>('POST /v1/trajectory', {
      body: { project_id: projectId, thread_id: threadId, format: 'messages', ...(cursor ? { cursor } : {}) },
    });
    messages.push(...response.messages);
    cursor = response.next_cursor;
    if (!cursor) break;
  }
  return messages;
}

export async function openRun(projectId: string, run: ToolCallRun): Promise<void> {
  const { url } = await call<{ url: string }>(`GET /api/v2/runs/${run.id}/url`, {
    params: { project_id: projectId, trace_id: run.trace_id },
  });
  window.langsmith.openUrl(url, { newTab: true });
}
