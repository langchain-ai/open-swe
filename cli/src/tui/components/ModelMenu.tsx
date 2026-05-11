import { Box, Text } from 'ink';
import { themeColor } from '@tui/theme.js';
import { ARROW_RIGHT } from '@tui/figures.js';
import type { ModelMenuProps } from '@types';

export const ModelMenu = ({ models, selectedIndex, currentModelId }: ModelMenuProps) => {
  const subtle = themeColor('subtle');
  const inactive = themeColor('inactive');
  const suggestion = themeColor('suggestion');
  const success = themeColor('success');
  return (
    <Box flexDirection="column" paddingX={1} marginTop={0}>
      {models.map((model, index) => {
        const isSelected = selectedIndex === index;
        const isCurrent = model.id === currentModelId;
        return (
          <Box key={model.id}>
            <Text color={isSelected ? suggestion : inactive}>
              {isSelected ? `${ARROW_RIGHT} ` : '  '}
            </Text>
            <Text bold color={isSelected ? suggestion : undefined}>
              {String(model.id).padStart(2, ' ')}. {model.label}
            </Text>
            <Text color={subtle}>{` (${model.name} · ${model.effort})`}</Text>
            {isCurrent ? <Text color={success}> · current</Text> : null}
          </Box>
        );
      })}
    </Box>
  );
};
