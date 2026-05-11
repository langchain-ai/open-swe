import { Box, Text } from 'ink';
import { useStore } from '@app/store.js';
import { modelOptions } from '@lib/models.js';
import { themeColor } from '@tui/theme.js';
import type { ModelConfig } from '@types';

const formatTokens = (n: number): string => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toString();
};

const modelShortLabel = (modelConfig: ModelConfig): string => {
  const opt = modelOptions.find(
    (o) => o.name === modelConfig.name && o.effort === modelConfig.effort,
  );
  return opt ? opt.label : `${modelConfig.name}·${modelConfig.effort}`;
};

const homePrefix = (p: string): string => {
  const home = process.env.HOME ?? '';
  if (home && p.startsWith(home)) {
    return '~' + p.slice(home.length);
  }
  return p;
};

export const StatusLine = () => {
  const tokenUsage = useStore((s) => s.tokenUsage);
  const modelConfig = useStore((s) => s.modelConfig);
  const subtle = themeColor('subtle');

  const opt = modelOptions.find(
    (o) => o.name === modelConfig.name && o.effort === modelConfig.effort,
  );
  const ctx = opt?.contextWindow ?? 0;
  const ctxLeft = ctx > 0 ? Math.max(0, Math.min(100, Math.round(((ctx - tokenUsage.total) / ctx) * 100))) : null;

  const dir = homePrefix(process.cwd());

  return (
    <Box width="100%" paddingX={1}>
      <Text color={subtle}>{modelShortLabel(modelConfig)}</Text>
      {tokenUsage.total > 0 ? (
        <>
          <Text color={subtle}>  ·  </Text>
          <Text color={subtle}>{formatTokens(tokenUsage.total)} tokens</Text>
        </>
      ) : null}
      {ctxLeft !== null && tokenUsage.total > 0 ? (
        <>
          <Text color={subtle}>  ·  </Text>
          <Text color={subtle}>{ctxLeft}% ctx</Text>
        </>
      ) : null}
      <Text color={subtle}>  ·  </Text>
      <Text color={subtle}>{dir}</Text>
    </Box>
  );
};
