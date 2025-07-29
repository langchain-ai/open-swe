#!/usr/bin/env node
import React, { useState, useEffect, useCallback } from "react";
import { render, Box, Text, useInput } from "ink";
import {
  startAuthServer,
  getAccessToken,
  getInstallationId,
} from "./auth-server.js";
import open from "open";
import { v4 as uuidv4 } from "uuid";
import {
  MANAGER_GRAPH_ID,
  OPEN_SWE_STREAM_MODE,
} from "@open-swe/shared/constants";
import { Client } from "@langchain/langgraph-sdk";
import { submitFeedback } from "./utils.js";
import { StreamingService } from "./streaming.js";

type StreamMode = "values" | "updates" | "messages";

const GITHUB_LOGIN_URL =
  process.env.GITHUB_LOGIN_URL || "http://localhost:3000/api/auth/github/login";

startAuthServer();

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

async function fetchUserRepos(token: string) {
  const allRepos = [];
  let page = 1;
  const perPage = 100;
  while (true) {
    const res = await fetch(
      `https://api.github.com/user/repos?per_page=${perPage}&page=${page}`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github.v3+json",
          "User-Agent": "open-swe-cli",
        },
      },
    );
    if (!res.ok) throw new Error("Failed to fetch repos");
    const repos = await res.json();
    allRepos.push(...repos);
    if (repos.length < perPage) break;
    page++;
  }
  return allRepos;
}

const RepoSearchSelect: React.FC<{
  repos: any[];
  // eslint-disable-next-line no-unused-vars
  onSelect: (repo: any) => void;
}> = ({ repos, onSelect }) => {
  const [search, setSearch] = useState("");
  const [highlighted, setHighlighted] = useState(0);
  const [isMessage, setIsMessage] = useState(false);

  const filtered = repos.filter((repo) =>
    repo.full_name.toLowerCase().includes(search.toLowerCase()),
  );
  const shown = filtered.slice(0, 10);

  useInput((input: string, key: { [key: string]: any }) => {
    if (isMessage) return;
    if (key.return) {
      if (shown.length > 0) {
        setIsMessage(true);
        onSelect(shown[highlighted]);
      }
    } else if (key.upArrow) {
      setHighlighted((h) => (h - 1 + shown.length) % shown.length);
    } else if (key.downArrow) {
      setHighlighted((h) => (h + 1) % shown.length);
    } else if (key.backspace || key.delete) {
      setSearch((prev) => prev.slice(0, -1));
      setHighlighted(0);
    } else if (input && !key.ctrl && !key.meta) {
      setSearch((prev) => prev + input);
      setHighlighted(0);
    }
  });

  if (isMessage) return null;

  return (
    <Box flexDirection="column">
      <Box>
        <Text>Search repositories: {search}</Text>
      </Box>
      {shown.length === 0 ? (
        <Box>
          <Text dimColor>No matches found.</Text>
        </Box>
      ) : (
        <Box flexDirection="column" marginTop={1}>
          {shown.map((_, idx) => (
            <Text key={shown[idx].id} dimColor={idx !== highlighted}>
              {idx === highlighted ? "> " : "  "}
              {shown[idx].full_name}
            </Text>
          ))}
        </Box>
      )}
      <Box marginTop={1}>
        <Text dimColor>Use ↑/↓ to navigate, Enter to select</Text>
      </Box>
    </Box>
  );
};

const App: React.FC = () => {
  const [authPrompt, setAuthPrompt] = useState<null | boolean>(null);
  const [authInput, setAuthInput] = useState("");
  const [exit, setExit] = useState(false);
  const [authStarted, setAuthStarted] = useState(false);
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [repos, setRepos] = useState<any[]>([]);
  const [selectedRepo, setSelectedRepo] = useState<any | null>(null);
  const [selectingRepo, setSelectingRepo] = useState(false);
  const [waitingForInstall, setWaitingForInstall] = useState(false);
  const [installChecked, setInstallChecked] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const [appSlug, setAppSlug] = useState(process.env.GITHUB_APP_NAME || "");
  const INSTALLATION_CALLBACK_URL = process.env.GITHUB_CALLBACK_URL || "";
  const [pollingForToken, setPollingForToken] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [prompt, setPrompt] = useState<string>("");
  const [plannerFeedback, setPlannerFeedback] = useState<string | null>(null);
  const [streamingPhase, setStreamingPhase] = useState<
    "streaming" | "awaitingFeedback" | "done"
  >("streaming");
  const [threadId, setThreadId] = useState<string | null>(null);
  const [plannerThreadId, setPlannerThreadId] = useState<string | null>(null);
  const [hasStartedChat, setHasStartedChat] = useState(false);
  const [client, setClient] = useState<Client | null>(null);
  const [loadingRepos, setLoadingRepos] = useState(false);
  const [loadingLogs, setLoadingLogs] = useState(false);

  const sendInterruptMessage = useCallback(
    async (message: string) => {
      if (!client || !threadId || !selectedRepo) {
        return;
      }

      setLogs((prev) => [...prev, `📤 Interrupt Response: "${message}"`]);

      try {
        const [owner, repoName] = selectedRepo.full_name.split("/");
        const interruptInput = {
          messages: [
            {
              id: uuidv4(),
              type: "human",
              content: [{ type: "text", text: message }],
            },
          ],
          targetRepository: {
            owner,
            repo: repoName,
            branch: selectedRepo.default_branch || "main",
          },
        };
        await client.runs.create(threadId, MANAGER_GRAPH_ID, {
          input: interruptInput,
          config: { recursion_limit: 400 },
          ifNotExists: "create",
          streamResumable: true,
          multitaskStrategy: "enqueue",
          streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
        });

        // Just submit the interrupt - existing planner session will pick it up automatically
        setLogs((prev) => [...prev, `✅ Interrupt sent to existing session`]);
      } catch (err: any) {
        setLogs((prev) => [...prev, `Error sending interrupt: ${err.message}`]);
      }
    },
    [client, threadId, selectedRepo, setLogs],
  );

  // On mount, check for existing token
  useEffect(() => {
    const token = getAccessToken();
    if (token) {
      setIsLoggedIn(true);
    }
  }, []);

  // After login, fetch and store user repos
  useEffect(() => {
    if (isLoggedIn && repos.length === 0 && !loadingRepos) {
      const token = getAccessToken();
      if (token) {
        setLoadingRepos(true);
        fetchUserRepos(token)
          .then((repos) => {
            setRepos(repos);
            setSelectingRepo(true);
            setLoadingRepos(false);
          })
          .catch((err) => {
            console.error("Failed to fetch repos:", err);
            setLoadingRepos(false);
          });
      }
    }
  }, [isLoggedIn, repos.length, loadingRepos]);

  // Poll for installation_id after opening install page
  useEffect(() => {
    let interval: ReturnType<typeof setInterval>;
    if (waitingForInstall) {
      interval = setInterval(() => {
        // Check if installation_id is present in config file
        const installationId = getInstallationId();
        if (installationId) {
          setInstallChecked(true);
          setWaitingForInstall(false);
        }
      }, 1500);
    }
    return () => clearInterval(interval);
  }, [waitingForInstall]);

  // Listen for Cmd+C/Ctrl+C to re-select repo
  useInput((input: string, key: { [key: string]: any }) => {
    if (installChecked && !waitingForInstall && key.return) {
      setInstallChecked(false);
      setSelectingRepo(false);
    }
    if (selectedRepo && (key.ctrl || key.meta) && input.toLowerCase() === "c") {
      setSelectingRepo(true);
      setSelectedRepo(null);
    }
  });

  // Handle yes/no input for auth prompt
  useInput((input: string, key: { [key: string]: any }) => {
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
      setPollingForToken(true);
    }
  }, [authPrompt, authStarted]);

  // Poll for token after auth flow starts
  useEffect(() => {
    if (pollingForToken && !isLoggedIn) {
      const interval = setInterval(() => {
        const token = getAccessToken();
        if (token) {
          setIsLoggedIn(true);
          setPollingForToken(false);
        }
      }, 1000);
      return () => clearInterval(interval);
    }
  }, [pollingForToken, isLoggedIn]);

  // Custom input for planner feedback (must be inside App)
  const PlannerFeedbackInput: React.FC = () => {
    const [input, setInput] = useState("");
    useInput((inputChar: string, key: { [key: string]: any }) => {
      if (streamingPhase !== "awaitingFeedback") return;
      if (key.return) {
        if (
          input.trim().toLowerCase() === "approve" ||
          input.trim().toLowerCase() === "deny"
        ) {
          setPlannerFeedback(input.trim().toLowerCase());
          setInput(""); // Clear input after submitting
        }
      } else if (key.backspace || key.delete) {
        setInput((prev) => prev.slice(0, -1));
      } else if (inputChar) {
        setInput((prev) => prev + inputChar);
      }
    });
    if (streamingPhase !== "awaitingFeedback") return null;
    return (
      <Box>
        <Text>Plan feedback (approve/deny): {input}</Text>
      </Box>
    );
  };

  // Streaming logic: when prompt is set, stream logs
  useEffect(() => {
    if (prompt && selectedRepo && streamingPhase === "streaming") {
      setPlannerFeedback(null);

      const streamingService = new StreamingService({
        setLogs,
        setPlannerThreadId,
        setStreamingPhase,
        setLoadingLogs,
        setClient,
        setThreadId,
      });

      streamingService.startNewSession(prompt, selectedRepo);
      setPrompt("");
    }
  }, [prompt, selectedRepo, streamingPhase]);

  // Add this where we handle planner feedback
  useEffect(() => {
    if (
      streamingPhase === "awaitingFeedback" &&
      plannerFeedback &&
      plannerThreadId
    ) {
      // Immediately switch to streaming mode to hide the feedback prompt
      setStreamingPhase("streaming");

      (async () => {
        await submitFeedback({
          plannerFeedback,
          plannerThreadId,
          selectedRepo,
          setLogs,
          setPlannerFeedback: () => setPlannerFeedback(null),
        });
      })();
    }
  }, [streamingPhase, plannerFeedback, selectedRepo, plannerThreadId]);

  // Loading repos after login
  if (isLoggedIn && loadingRepos) {
    return (
      <Box flexDirection="column" padding={1}>
        <LoadingSpinner text="Loading your repositories" />
      </Box>
    );
  }

  // Repo selection UI
  if (isLoggedIn && repos.length > 0 && (selectingRepo || !selectedRepo)) {
    return (
      <Box flexDirection="column" padding={1}>
        <Box justifyContent="center" marginBottom={1}>
          <Text bold>LangChain Open SWE CLI</Text>
        </Box>
        <Box flexDirection="column" marginBottom={1}>
          <Text>Select a repository to work with (type to search):</Text>
        </Box>
        <Box
          borderStyle="round"
          borderColor="white"
          paddingX={2}
          paddingY={1}
          marginTop={1}
          marginBottom={1}
        >
          <RepoSearchSelect
            repos={repos}
            onSelect={async (repo) => {
              let slug = appSlug;
              const installationId = getInstallationId();
              setSelectedRepo(repo);
              setSelectingRepo(false);
              if (installationId) {
                setInstallChecked(true);
                setWaitingForInstall(false);
                setInstallError(null);
                return;
              }
              if (!slug) {
                console.log(
                  "Please enter your GitHub App slug (as in https://github.com/apps/<slug>):",
                );
                process.stdin.resume();
                process.stdin.setEncoding("utf8");
                slug = await new Promise((resolve) => {
                  process.stdin.once("data", (data) =>
                    resolve(String(data).trim()),
                  );
                });
                setAppSlug(slug);
              }
              const installUrl = `https://github.com/apps/${slug}/installations/new?redirect_uri=${encodeURIComponent(INSTALLATION_CALLBACK_URL)}`;
              console.log(
                "Opening GitHub App installation page in your browser...",
              );
              await open(installUrl);
              setWaitingForInstall(true);
              setInstallChecked(false);
              setInstallError(null);
            }}
          />
        </Box>
        {waitingForInstall && (
          <Box flexDirection="column" marginTop={1}>
            <Text>Waiting for GitHub App installation to complete...</Text>
            <Text dimColor>
              After installing the app, return here to continue.
            </Text>
          </Box>
        )}
        {installChecked && !waitingForInstall && (
          <Box flexDirection="column" marginTop={1}>
            <Text>GitHub App installation detected! You can now proceed.</Text>
            <Text dimColor>Press Enter to continue.</Text>
          </Box>
        )}
        {installError && (
          <Box marginTop={1}>
            <Text>{installError}</Text>
          </Box>
        )}
      </Box>
    );
  }

  // Main UI: logs area + input prompt
  if (isLoggedIn && selectedRepo) {
    // Calculate available space for logs based on whether welcome message is shown
    const headerHeight = 0; // Welcome message is now above input bar, not at top
    const inputHeight = 4; // Fixed input area height (increased due to padding)
    const welcomeHeight = hasStartedChat ? 0 : 8; // Welcome message height when shown
    const paddingHeight = 3; // Extra padding to prevent overlap
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
                    dimColor={!log.startsWith("[AI]")}
                    bold={log.startsWith("[AI]")}
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
######## ##     ## ##    ##  ######    ######  ##     ## ##     ## #### ##    ## 
`}
              </Text>
            </Box>
            <Box marginTop={2} marginBottom={1}>
              <Text dimColor>
                Describe your coding problem. It'll run in the sandbox and a PR
                will be created.
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
          {streamingPhase === "awaitingFeedback" ? (
            <PlannerFeedbackInput />
          ) : (
            <Box>
              {!hasStartedChat ? (
                <CustomInput
                  onSubmit={(value) => {
                    setHasStartedChat(true);
                    setPrompt(value);
                  }}
                />
              ) : (
                <CustomInput
                  onSubmit={(value) => {
                    sendInterruptMessage(value);
                  }}
                />
              )}
            </Box>
          )}
        </Box>
      </Box>
    );
  }

  // Auth prompt UI
  if (!isLoggedIn && authPrompt === null) {
    return (
      <Box flexDirection="column" padding={1}>
        <Box
          borderStyle="round"
          borderColor="white"
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

  // Fallback
  return <Box flexDirection="column" padding={1}></Box>;
};

render(<App />);
