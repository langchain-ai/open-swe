import { Box, Text } from 'ink';
import { themeColor } from '@tui/theme.js';
import { ARROW_RIGHT } from '@tui/figures.js';
import type { FileSearchMenuProps } from '@types';

export const FileSearchMenu = ({ matches, selectedIndex }: FileSearchMenuProps) => {
  const inactive = themeColor('inactive');
  const suggestion = themeColor('suggestion');
  return (
    <Box flexDirection="column" paddingX={1} marginTop={0}>
      {matches.length > 0 ? (
        matches.map((match, index) => {
          const isSelected = selectedIndex === index;
          return (
            <Box key={match}>
              <Text color={isSelected ? suggestion : inactive}>
                {isSelected ? `${ARROW_RIGHT} ` : '  '}
              </Text>
              <Text color={isSelected ? suggestion : undefined}>{match}</Text>
            </Box>
          );
        })
      ) : (
        <Text color={inactive}>No file matches.</Text>
      )}
    </Box>
  );
};
