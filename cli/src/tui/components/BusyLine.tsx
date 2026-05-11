import { Box, Text } from 'ink';
import { Spinner } from './Spinner.js';
import { themeColor } from '@tui/theme.js';

type Props = {
  label: string;
};

export const BusyLine = ({ label }: Props) => {
  const subtle = themeColor('subtle');
  return (
    <Box marginTop={1}>
      <Spinner />
      <Text> </Text>
      <Text color={themeColor('brand')} bold>
        {label}
      </Text>
      <Text color={subtle}> · esc to interrupt</Text>
    </Box>
  );
};
