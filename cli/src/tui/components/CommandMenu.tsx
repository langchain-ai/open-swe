import { Box, Text } from 'ink';
import { themeColor } from '@tui/theme.js';
import { ARROW_RIGHT } from '@tui/figures.js';
import type { CommandMenuProps } from '@types';

export const CommandMenu = ({ commands, selectedIndex }: CommandMenuProps) => {
  const subtle = themeColor('subtle');
  const inactive = themeColor('inactive');
  const suggestion = themeColor('suggestion');
  return (
    <Box flexDirection="column" paddingX={1} marginTop={0}>
      {commands.map((command, index) => {
        const isSelected = selectedIndex === index;
        return (
          <Box key={command.name}>
            <Text color={isSelected ? suggestion : inactive}>
              {isSelected ? `${ARROW_RIGHT} ` : '  '}
            </Text>
            <Text bold color={isSelected ? suggestion : undefined}>
              /{command.name}
            </Text>
            <Text color={subtle}>{` ${command.description}`}</Text>
          </Box>
        );
      })}
    </Box>
  );
};
