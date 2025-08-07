#!/usr/bin/env node
import React, { useState, useEffect } from "react";
import { render, Box, Text, useInput } from "ink";
import { Command } from "commander";
import { OPEN_SWE_CLI_VERSION } from "./constants.js";
import dotenv from "dotenv";
dotenv.config();

// Handle graceful exit on Ctrl+C and Ctrl+K
process.on("SIGINT", () => {
  console.log("\n👋 Goodbye!");
  process.exit(0);
});

process.on("SIGTERM", () => {
  console.log("\n👋 Goodbye!");
  process.exit(0);
});

import { submitFeedback } from "./utils.js";
import { StreamingService } from "./streaming.js";

// Parse command line arguments with Commander
const program = new Command();

program
  .name("open-swe")
  .description("Open SWE CLI - Local Mode")
  .version(OPEN_SWE_CLI_VERSION)
  .helpOption("-h, --help", "Display help for command")
  .parse();

// Always run in local mode
process.env.OPEN_SWE_LOCAL_MODE = "true";

console.log("🏠 Starting Open SWE CLI in Local Mode");
console.log("   Working directory:", process.cwd());
console.log("   No GitHub authentication required");
console.log("");

const LoadingSpinner: React.FC<{ text: string }> = ({ text }) => {
  const [dots, setDots] = useState("");

  useEffect(() => {
    const interval = setInterval(() => {
      setDots((prev) => (prev.length >= 3 ? "" : prev + "."));
    }, 500);
    return () => clearInterval(interval);
  }, []);

  return (
    <Box justifyContent="center" paddingY={2}>
      <Text>
        {text}
        {dots}
      </Text>
    </Box>
  );
};
// eslint-disable-next-line no-unused-vars
const CustomInput: React.FC<{ onSubmit: (value: string) => void }> = ({
  onSubmit,
}) => {
  const [input, setInput] = useState("");
  const [isSubmitted, setIsSubmitted] = useState(false);

  useInput((inputChar: string, key: { [key: string]: any }) => {
    if (isSubmitted) return;

    // Handle Ctrl+K for exit
    if (key.ctrl && inputChar.toLowerCase() === "k") {
      console.log("\n👋 Goodbye!");
      process.exit(0);
    }

    if (key.return) {
      if (input.trim()) {
        // Only submit if there's actual content
        setIsSubmitted(true);
        onSubmit(input);
        // Reset for next input
        setTimeout(() => {
          setInput("");
          setIsSubmitted(false);
        }, 100);
      }
    } else if (key.backspace || key.delete) {
      setInput((prev) => prev.slice(0, -1));
    } else if (inputChar) {
      setInput((prev) => prev + inputChar);
    }
  });

  return (
    <Box>
      <Text>&gt; {input}</Text>
    </Box>
  );
};

const App: React.FC = () => {
  const [logs, setLogs] = useState<string[]>([]);
  const [plannerFeedback, setPlannerFeedback] = useState<string | null>(null);
  const [streamingPhase, setStreamingPhase] = useState<
    "streaming" | "awaitingFeedback" | "done"
  >("streaming");
  const [plannerThreadId, setPlannerThreadId] = useState<string | null>(null);
  const [hasStartedChat, setHasStartedChat] = useState(false);
  const [loadingLogs, setLoadingLogs] = useState(false);

  const PlannerFeedbackInput: React.FC = () => {
    const [selectedOption, setSelectedOption] = useState<
      "approve" | "deny" | null
    >(null);

    useInput((inputChar: string, key: { [key: string]: any }) => {
      if (streamingPhase !== "awaitingFeedback") return;

      // Handle Ctrl+K for exit
      if (key.ctrl && inputChar.toLowerCase() === "k") {
        console.log("\n👋 Goodbye!");
        process.exit(0);
      }

      if (key.return && selectedOption) {
        setPlannerFeedback(selectedOption);
        setSelectedOption(null);
      } else if (key.leftArrow) {
        setSelectedOption("approve");
      } else if (key.rightArrow) {
        setSelectedOption("deny");
      }
    });

    if (streamingPhase !== "awaitingFeedback") {
      return null;
    }

    return (
      <Box flexDirection="row" alignItems="center" gap={2}>
        <Text>Plan feedback: </Text>
        <Text
          color={selectedOption === "approve" ? "black" : "white"}
          bold={selectedOption === "approve"}
        >
          {selectedOption === "approve" ? "▶ " : "  "}Approve
        </Text>
        <Text
          color={selectedOption === "deny" ? "black" : "white"}
          bold={selectedOption === "deny"}
        >
          {selectedOption === "deny" ? "▶ " : "  "}Deny
        </Text>
        <Text dimColor>(Use ←/→ to select, Enter to confirm)</Text>
      </Box>
    );
  };

  // Handle planner feedback
  useEffect(() => {
    if (
      streamingPhase === "awaitingFeedback" &&
      plannerFeedback &&
      plannerThreadId
    ) {
      (async () => {
        await submitFeedback({
          plannerFeedback,
          plannerThreadId,
          setLogs,
          setPlannerFeedback: () => setPlannerFeedback(null),
          setStreamingPhase,
        });
      })();
    }
  }, [streamingPhase, plannerFeedback, plannerThreadId]);

  const headerHeight = 0;
  const inputHeight = 4;
  const welcomeHeight = hasStartedChat ? 0 : 8;
  const paddingHeight = 3;
  const availableLogHeight = Math.max(
    5,
    process.stdout.rows -
      headerHeight -
      inputHeight -
      welcomeHeight -
      paddingHeight,
  );

  // Always show the most recent logs (auto-scroll to bottom)
  const visibleLogs =
    logs.length > availableLogHeight ? logs.slice(-availableLogHeight) : logs;

  return (
    <Box flexDirection="column" height={process.stdout.rows}>
      {/* Auto-scrolling logs area - strict boundary container */}
      <Box
        height={availableLogHeight}
        flexDirection="column"
        paddingX={1}
        paddingBottom={1}
        overflowY="hidden"
        flexShrink={0}
        justifyContent="flex-end"
      >
        <Box flexDirection="column">
          {loadingLogs && logs.length === 0 ? (
            <LoadingSpinner text="Starting agent" />
          ) : (
            visibleLogs.map((log, index) => (
              <Box key={`${logs.length}-${index}`}>
                <Text
                  dimColor={
                    !log.startsWith("[AI]") && !log.includes("PROPOSED PLAN")
                  }
                  bold={log.startsWith("[AI]") || log.includes("PROPOSED PLAN")}
                >
                  {log}
                </Text>
              </Box>
            ))
          )}
        </Box>
      </Box>

      {/* Welcome message right above input bar */}
      {!hasStartedChat ? (
        <Box flexDirection="column" paddingX={1}>
          <Box>
            <Text>
              {`

##          ###    ##    ##  ######    ######  ##     ##    ###    #### ##    ## 
##         ## ##   ###   ## ##    ##  ##    ## ##     ##   ## ##    ##  ###   ## 
##        ##   ##  ####  ## ##        ##       ##     ##  ##   ##   ##  ####  ## 
##       ##     ## ## ## ## ##   #### ##       ######### ##     ##  ##  ## ## ## 
##       ######### ##  #### ##    ##  ##       ##     ## #########  ##  ##  #### 
##       ##     ## ##   ### ##    ##  ##    ## ##     ## ##     ##  ##  ##   ### 
######## ##     ## ##    ##  ######    ######  ##     ## ##     ## #### ##    ## OPEN SWE CLI
`}
            </Text>
          </Box>
        </Box>
      ) : (
        <Box height={8} />
      )}

      {/* Fixed input area at bottom */}
      <Box
        flexDirection="column"
        paddingX={2}
        borderStyle="single"
        borderTop
        height={3}
        flexShrink={0}
        justifyContent="center"
      >
        <Box>
          {streamingPhase === "awaitingFeedback" ? (
            <PlannerFeedbackInput />
          ) : !hasStartedChat ? (
            <CustomInput
              onSubmit={(value) => {
                setHasStartedChat(true);
                setPlannerFeedback(null);

                const streamingService = new StreamingService({
                  setLogs,
                  setPlannerThreadId,
                  setStreamingPhase,
                  setLoadingLogs,
                });

                streamingService.startNewSession(value);
              }}
            />
          ) : (
            <Box>
              <Text>Streaming...</Text>
            </Box>
          )}
        </Box>
      </Box>

      {/* Local mode indicator underneath the input bar */}
      <Box paddingX={2} paddingY={0}>
        <Text>
          Working on {process.env.OPEN_SWE_LOCAL_PROJECT_PATH} • Ctrl+K to exit
        </Text>
      </Box>
    </Box>
  );
};

render(<App />);
