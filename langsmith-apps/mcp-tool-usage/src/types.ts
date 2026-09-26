import type { Run, TracerSession } from 'langsmith/schemas';

export type Project = Pick<TracerSession, 'id'> & { name: string };

export type WindowKey = '1d' | '7d';

export interface ToolUsage {
  runName: string;
  connection: string;
  tool: string;
  count: number;
}

export interface TopTools {
  tools: ToolUsage[];
  total: number;
}

export interface ChartPoint {
  series_id: string;
  timestamp: string;
  value: number | null;
  group?: string | null;
}

export interface ChartPreviewResponse {
  data: ChartPoint[];
}

// v2 runs/query returns only the selected fields, plus two the SDK's Run doesn't model.
export type ToolCallRun = Pick<Run, 'id'> &
  Required<Pick<Run, 'trace_id' | 'status'>> & {
    start_time: string;
    thread_id: string | null;
    outputs_preview: string | null;
  };

export interface ToolCallRunsResponse {
  items: ToolCallRun[];
  next_cursor?: string | null;
}

export interface ThreadCandidate {
  threadId: string;
  calls: ToolCallRun[];
}

export interface TextBlock {
  type: 'text';
  text: string;
}

export interface ReasoningBlock {
  type: 'reasoning';
  reasoning: string;
}

export interface ToolCallBlock {
  type: 'tool_call';
  id: string;
  name: string;
  args: unknown;
}

export type ContentBlock = TextBlock | ReasoningBlock | ToolCallBlock | { type: string };

export type MessageRole = 'system' | 'human' | 'ai' | 'tool';

export interface TrajectoryMessage {
  id: string | null;
  role: MessageRole;
  content: string | ContentBlock[];
  tool_call_id: string | null;
}

export interface TrajectoryResponse {
  messages: TrajectoryMessage[];
  next_cursor: string | null;
}
