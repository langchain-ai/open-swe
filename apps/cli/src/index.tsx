#!/usr/bin/env node
import React, { useState, useEffect } from "react";
import { render, Box, Text, useInput } from "ink";
import { startAuthServer, getAccessToken } from "./auth-server.js";
import open from "open";
import TerminalInterface from "./TerminalInterface.js";

const GITHUB_LOGIN_URL =
  process.env.GITHUB_LOGIN_URL || "http://localhost:3000/api/auth/github/login";

const CustomInput: React.FC<{ onSubmit: () => void }> = ({ onSubmit }) => {
  const [input, setInput] = useState("");
  const [isSubmitted, setIsSubmitted] = useState(false);

  useInput((inputChar, key) => {
    if (isSubmitted) return;
    if (key.return) {
      setIsSubmitted(true);
      onSubmit();
    } else if (key.backspace || key.delete) {
      setInput((prev) => prev.slice(0, -1));
    } else if (inputChar) {
      setInput((prev) => prev + inputChar);
    }
  });

  if (isSubmitted) return null;

  return (
    <Box>
      <Text color="cyan">&gt; {input}</Text>
    </Box>
  );
};

const App: React.FC = () => {
  const [authPrompt, setAuthPrompt] = useState<null | boolean>(null);
  const [authInput, setAuthInput] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [exit, setExit] = useState(false);
  const [authStarted, setAuthStarted] = useState(false);
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  // On mount, check for existing token
  useEffect(() => {
    const token = getAccessToken();
    if (token) {
      setIsLoggedIn(true);
    }
  }, []);

  // Poll for token after auth flow starts
  useEffect(() => {
    if (authStarted && !isLoggedIn) {
      const interval = setInterval(() => {
        const token = getAccessToken();
        if (token) {
          setIsLoggedIn(true);
          clearInterval(interval);
        }
      }, 1000);
      return () => clearInterval(interval);
    }
  }, [authStarted, isLoggedIn]);

  // Handle yes/no input for auth prompt
  useInput((input, key) => {
    if (authPrompt === null && !isLoggedIn) {
      if (key.return) {
        if (authInput.toLowerCase() === "y") {
          setAuthPrompt(true);
        } else if (authInput.toLowerCase() === "n") {
          setAuthPrompt(false);
          setExit(true);
        }
      } else if (key.backspace || key.delete) {
        setAuthInput((prev) => prev.slice(0, -1));
      } else if (input && authInput.length < 1) {
        // Only allow a single character (y/n)
        setAuthInput(input);
      }
    }
  });

  // Exit the process safely after render
  useEffect(() => {
    if (exit) {
      process.exit(0);
    }
  }, [exit]);

  // Start auth server and open browser when user says yes
  useEffect(() => {
    if (authPrompt === true && !authStarted) {
      setAuthStarted(true);
      startAuthServer();
      open(GITHUB_LOGIN_URL);
    }
  }, [authPrompt, authStarted]);

  if (isLoggedIn) {
    return (
      <Box flexDirection="column" height={"100%"}>
        <TerminalInterface
          message={message}
          setMessage={() => setMessage(null)}
          CustomInput={CustomInput}
        />
        <Box flexGrow={1} />
        <Box width="100%" justifyContent="flex-end">
          <Text color="gray" dimColor>
            logged in
          </Text>
        </Box>
      </Box>
    );
  }

  if (authPrompt === null) {
    return (
      <Box flexDirection="column" padding={1}>
        <Box justifyContent="center" marginBottom={1}>
          <Text bold color="magenta">
            LangChain Open SWE CLI
          </Text>
        </Box>
        <Box
          borderStyle="round"
          borderColor="gray"
          paddingX={2}
          paddingY={1}
          marginTop={1}
          marginBottom={1}
        >
          <Text>
            Do you want to start the GitHub authentication flow? (y/n){" "}
            {authInput}
          </Text>
        </Box>
      </Box>
    );
  }

  return (
    <Box flexDirection="column" padding={1}>
      <Box justifyContent="center" marginBottom={1}>
        <Text bold color="magenta">
          LangChain Open SWE CLI
        </Text>
      </Box>
      {!message && <CustomInput onSubmit={() => setMessage(null)} />}
      {message && (
        <Box marginTop={1}>
          <Text color="green">You typed: {message}</Text>
        </Box>
      )}
    </Box>
  );
};

render(<App />);
