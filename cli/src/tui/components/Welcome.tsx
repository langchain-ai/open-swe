import { Box, Text } from 'ink';
import path from 'path';
import { themeColor } from '@tui/theme.js';
import { ARROW_RIGHT_THIN } from '@tui/figures.js';
import type { ModelConfig } from '@types';

type Props = {
  modelConfig: ModelConfig;
  cwd?: string;
};

const homePrefix = (p: string): string => {
  const home = process.env.HOME ?? '';
  if (home && p.startsWith(home)) {
    return '~' + p.slice(home.length);
  }
  return p;
};

export const Welcome = ({ modelConfig, cwd }: Props) => {
  const dir = homePrefix(cwd ?? process.cwd());
  const brand = themeColor('brand');
  const subtle = themeColor('subtle');
  const inactive = themeColor('inactive');
  const suggestion = themeColor('suggestion');

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={brand}
      paddingX={2}
      paddingY={0}
      marginBottom={1}
    >
      <Box>
        <Text color={brand}>{ARROW_RIGHT_THIN}_ </Text>
        <Text bold>coda</Text>
      </Box>
      <Text> </Text>
      <Box>
        <Text color={inactive}>{'model:     '}</Text>
        <Text color={suggestion}>{modelConfig.name} {modelConfig.effort}</Text>
        <Text color={subtle}>{'    '}/model to change</Text>
      </Box>
      <Box>
        <Text color={inactive}>{'directory: '}</Text>
        <Text color={suggestion}>{dir}</Text>
      </Box>
    </Box>
  );
};
