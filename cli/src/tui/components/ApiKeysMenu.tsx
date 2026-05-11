import { Box, Text } from 'ink';
import { themeColor } from '@tui/theme.js';
import { ARROW_RIGHT } from '@tui/figures.js';
import type { ApiKeyMenuItem } from '@types';

type Props = {
  items: ApiKeyMenuItem[];
  selectedIndex: number;
};

export const ApiKeysMenu = ({ items, selectedIndex }: Props) => {
  const subtle = themeColor('subtle');
  const inactive = themeColor('inactive');
  const suggestion = themeColor('suggestion');
  const danger = themeColor('error');

  return (
    <Box flexDirection="column" paddingX={1} marginTop={0}>
      {items.map((item, index) => {
        const isSelected = selectedIndex === index;
        const isDelete = item.action === 'delete';
        const labelColor = isSelected
          ? isDelete ? danger : suggestion
          : undefined;
        return (
          <Box key={`${item.provider}-${item.action}`}>
            <Text color={isSelected ? (isDelete ? danger : suggestion) : inactive}>
              {isSelected ? `${ARROW_RIGHT} ` : '  '}
            </Text>
            <Text bold color={labelColor}>
              {item.label}
            </Text>
            <Text color={subtle}>{`  (${item.detail})`}</Text>
          </Box>
        );
      })}
      <Box marginTop={1}>
        <Text color={subtle}>
          ↑/↓ to navigate · enter to select · esc to close
        </Text>
      </Box>
    </Box>
  );
};
