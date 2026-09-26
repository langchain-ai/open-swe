import { EmptyState } from '@langchain/macaw-components/EmptyState';
import { GroupedTabs } from '@langchain/macaw-components/GroupedTabs';
import { Text } from '@langchain/macaw-components/Text';
import { useEffect, useState } from 'react';
import { DEFAULT_PROJECT_NAME, fetchProjects, fetchTopTools, findProjectByName } from './api';
import { GuideState } from './components/GuideState';
import { SearchableSelect } from './components/SearchableSelect';
import { ThreadView } from './components/ThreadView';
import { ToolRanking } from './components/ToolRanking';
import type { Project, TopTools, WindowKey } from './types';

export function App(_props: { data: unknown; metadata?: RenderMetadata }) {
  const [project, setProject] = useState<Project | null>(null);
  const [resolving, setResolving] = useState(true);
  const [resolveFailed, setResolveFailed] = useState(false);
  const [window, setWindow] = useState<WindowKey>('7d');
  const [top, setTop] = useState<TopTools | null>(null);
  const [topState, setTopState] = useState<'ready' | 'loading' | 'error'>('loading');
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    findProjectByName(DEFAULT_PROJECT_NAME)
      .then(setProject)
      .catch((e) => {
        console.error('Failed to resolve default project', e);
        setResolveFailed(true);
      })
      .finally(() => setResolving(false));
  }, []);

  useEffect(() => {
    if (!project) return;
    let cancelled = false;
    setTopState('loading');
    fetchTopTools(project.id, window)
      .then((result) => {
        if (cancelled) return;
        setTop(result);
        setTopState('ready');
        setSelected((current) =>
          current && result.tools.some((tool) => tool.runName === current)
            ? current
            : (result.tools[0]?.runName ?? null)
        );
      })
      .catch((e) => {
        console.error('Failed to load top tools', e);
        if (!cancelled) setTopState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [project, window]);

  const selectedTool = top?.tools.find((tool) => tool.runName === selected) ?? null;

  return (
    <div className="flex min-h-screen flex-col gap-space-4 bg-surface-level-1 p-space-4 md:p-space-6">
      <header className="flex flex-wrap items-end justify-between gap-space-3">
        <div className="flex flex-col gap-space-1">
          <Text variant="h2">MCP Tool Usage</Text>
          <Text variant="sm" color="tertiary">
            Most-called MCP tools, with a representative thread for each.
          </Text>
        </div>
        <div className="flex flex-wrap items-center gap-space-2">
          <SearchableSelect<Project>
            value={project?.id ?? ''}
            valueLabel={project?.name}
            onSelect={(next) => {
              setSelected(null);
              setResolveFailed(false);
              setProject(next);
            }}
            fetchPage={fetchProjects}
            placeholder="Select a project…"
            searchPlaceholder="Search projects by name…"
            emptyLabel="No tracing projects in this workspace"
            className="w-64"
          />
          <GroupedTabs<WindowKey>
            size="sm"
            value={window}
            onChange={setWindow}
            options={[
              { value: '1d', display: '24h' },
              { value: '7d', display: '7d' },
            ]}
          />
        </div>
      </header>

      {renderBody()}
    </div>
  );

  function renderBody() {
    if (resolving) return <GuideState spinner heading="Loading project…" />;
    if (!project) {
      return resolveFailed ? (
        <GuideState
          tone="error"
          heading="Couldn't reach the LangSmith API"
          subtext="Check your access, then reload or pick a project above."
        />
      ) : (
        <GuideState
          heading="Pick a tracing project"
          subtext={`No project named ${DEFAULT_PROJECT_NAME} in this workspace.`}
        />
      );
    }
    return (
      <div className="grid min-w-0 gap-space-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:items-start">
        <ToolRanking data={top} state={topState} selected={selected} onSelect={setSelected} />
        {selectedTool ? (
          <ThreadView projectId={project.id} tool={selectedTool} window={window} />
        ) : (
          topState === 'ready' && <EmptyState title="Select a tool to see a thread" size="sm" />
        )}
      </div>
    );
  }
}
