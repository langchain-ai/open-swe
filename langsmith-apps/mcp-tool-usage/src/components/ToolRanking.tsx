import { ChartCard } from '@langchain/macaw-components/ChartCard';
import { EmptyState } from '@langchain/macaw-components/EmptyState';
import { TopList, type TopListItem } from '@langchain/macaw-components/TopList';
import { getFillChartColorForKey } from '@langchain/macaw-components/utils/chartColors';
import { useMemo } from 'react';
import { formatCount } from '../lib/tools';
import type { TopTools } from '../types';

type RankedTool = TopListItem & { connection: string };

const ROW_HEIGHT = 30;
const AXIS_HEIGHT = 48;

export function ToolRanking({
  data,
  state,
  selected,
  onSelect,
}: {
  data: TopTools | null;
  state: 'ready' | 'loading' | 'error';
  selected: string | null;
  onSelect: (runName: string) => void;
}) {
  const items = useMemo<RankedTool[]>(
    () =>
      (data?.tools ?? []).map((tool) => ({
        id: tool.runName,
        label: `${tool.connection} · ${tool.tool}`,
        value: tool.count,
        connection: tool.connection,
        color: getFillChartColorForKey(tool.connection),
      })),
    [data]
  );

  const shown = items.reduce((sum, item) => sum + item.value, 0);
  const description = data
    ? `${formatCount(data.total)} MCP calls · top ${items.length} cover ${
        data.total ? Math.round((shown / data.total) * 100) : 0
      }% · click a bar`
    : 'Tool runs tagged with mcp_tool_name';

  return (
    <ChartCard
      title="Top MCP Tools"
      description={description}
      state={state}
      skeletonVariant="bar"
      variant="full-width"
    >
      {items.length === 0 ? (
        <EmptyState title="No MCP tool calls in this window" size="sm" />
      ) : (
        // TopList fills its parent, so the parent needs a definite height.
        <div style={{ height: items.length * ROW_HEIGHT + AXIS_HEIGHT }}>
          <TopList
            aria-label="Top MCP tools by call count"
            items={items}
            formatValue={formatCount}
            valueAxisLabel="Calls"
            categoryLabelWidth={260}
            activeItemId={selected}
            onItemActivate={(item) => onSelect(item.id)}
          />
        </div>
      )}
    </ChartCard>
  );
}
