import React from "react";
import { Box, Text } from "ink";

interface TerminalInterfaceProps {
  message: string | null;
  setMessage: () => void;
  CustomInput: React.FC<{ onSubmit: () => void }>;
  repoName: string;
}

const TerminalInterface: React.FC<TerminalInterfaceProps> = ({
  message,
  setMessage,
  CustomInput,
  repoName,
}) => {
  return (
    <Box flexDirection="column" padding={1}>
      <Box justifyContent="center" marginBottom={0}>
        <Text bold>LangChain Open SWE CLI</Text>
      </Box>
      <Box flexDirection="column">
        <Text>Describe your coding task in as much detail as possible...</Text>
      </Box>
      <Box
        borderStyle="round"
        borderColor="gray"
        paddingX={2}
        paddingY={1}
        marginTop={0}
        marginBottom={0}
      >
        <CustomInput onSubmit={() => setMessage()} />
      </Box>
      {message && (
        <Box marginTop={1}>
          <Text color="green">You typed: {message}</Text>
        </Box>
      )}
      {repoName && (
        <Box marginTop={0} marginBottom={0}>
          <Text color="gray">Repository: {repoName}</Text>
        </Box>
      )}
    </Box>
  );
};

export default TerminalInterface;
