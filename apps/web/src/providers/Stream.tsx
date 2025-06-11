import React, {
  createContext,
  useContext,
  ReactNode,
  useState,
  useEffect,
} from "react";
import { useStream } from "@langchain/langgraph-sdk/react";
import {
  uiMessageReducer,
  isUIMessage,
  isRemoveUIMessage,
  type UIMessage,
  type RemoveUIMessage,
} from "@langchain/langgraph-sdk/react-ui";
import { useQueryState } from "nuqs";
import { LangGraphLogoSVG } from "@/components/icons/langgraph";
import { useThreads } from "./Thread";
import { TooltipIconButton } from "@/components/ui/tooltip-icon-button";
import { Copy, CopyCheck, ArrowRight } from "lucide-react";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { GitHubSVG } from "@/components/icons/github";
import { useGitHubToken } from "@/hooks/useGitHubToken";
import { GraphState, GraphUpdate } from "@open-swe/shared/open-swe/types";

const useTypedStream = useStream<
  GraphState,
  {
    UpdateType: GraphUpdate;
    CustomEventType: UIMessage | RemoveUIMessage;
  }
>;

type StreamContextType = ReturnType<typeof useTypedStream>;
const StreamContext = createContext<StreamContextType | undefined>(undefined);

async function sleep(ms = 4000) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const StreamSession = ({
  children,
  apiUrl,
  assistantId,
  githubToken,
}: {
  children: ReactNode;
  apiUrl: string;
  assistantId: string;
  githubToken: string;
}) => {
  const [threadId, setThreadId] = useQueryState("threadId");
  const { refreshThreads, setThreads } = useThreads();

  const githubAccessToken =
    document.cookie
      .split("; ")
      .find((row) => row.startsWith("x-github_access_token="))
      ?.split("=")[1] || "";
  const streamValue = useTypedStream({
    apiUrl,
    assistantId,
    reconnectOnMount: true,
    threadId: threadId ?? null,
    defaultHeaders: {
      "x-github-installation-token": githubToken,
      "x-github-access-token": githubAccessToken,
    },
    onCustomEvent: (event, options) => {
      if (isUIMessage(event) || isRemoveUIMessage(event)) {
        options.mutate((prev) => {
          const ui = uiMessageReducer(prev.ui ?? [], event);
          return { ...prev, ui };
        });
      }
    },
    onThreadId: (id) => {
      setThreadId(id);

      sleep().then(() => {
        refreshThreads().catch(console.error);
      });
    },
  });

  return (
    <StreamContext.Provider value={streamValue}>
      {children}
    </StreamContext.Provider>
  );
};

export const StreamProvider: React.FC<{ children: ReactNode }> = ({
  children,
}) => {
  const baseCopyTooltipText = "Copy environment variables";
  const [copyTooltipText, setCopyTooltipText] = useState(baseCopyTooltipText);

  const apiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL ?? "";
  const assistantId: string | undefined =
    process.env.NEXT_PUBLIC_ASSISTANT_ID ?? "";

  const [isAuth, setIsAuth] = useState<boolean | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const [hasGitHubAppInstalled, setHasGitHubAppInstalled] = useState<
    boolean | null
  >(() => {
    if (typeof window === "undefined") return null;
    const cached = localStorage.getItem("github_app_installed");
    return cached === "true" ? true : cached === "false" ? false : null;
  });

  const [isCheckingAppInstallation, setIsCheckingAppInstallation] =
    useState(false);
  const {
    token: githubToken,
    fetchToken: fetchGitHubToken,
    isLoading: isTokenLoading,
  } = useGitHubToken();

  useEffect(() => {
    checkAuthStatus();
  }, []);

  useEffect(() => {
    if (isAuth) {
      const cachedInstallationStatus = localStorage.getItem(
        "github_app_installed",
      );
      if (cachedInstallationStatus === "true") {
        setHasGitHubAppInstalled(true);
        // Fetch token if we don't have one yet
        if (!githubToken && !isTokenLoading) {
          fetchGitHubToken();
        }
      } else if (cachedInstallationStatus === "false") {
        setHasGitHubAppInstalled(false);
      } else {
        checkGitHubAppInstallation();
      }
    }
  }, [isAuth, githubToken, isTokenLoading]);

  const checkAuthStatus = async () => {
    try {
      const response = await fetch("/api/auth/status");
      const data = await response.json();
      setIsAuth(data.authenticated);
    } catch (error) {
      console.error("Error checking auth status:", error);
      setIsAuth(false);
    }
  };

  const checkGitHubAppInstallation = async () => {
    setIsCheckingAppInstallation(true);
    try {
      const response = await fetch("/api/github/repositories");
      if (response.ok) {
        setHasGitHubAppInstalled(true);
        localStorage.setItem("github_app_installed", "true");

        await fetchGitHubToken();
      } else {
        const errorData = await response.json();
        if (errorData.error.includes("installation")) {
          setHasGitHubAppInstalled(false);
          localStorage.setItem("github_app_installed", "false");
        } else {
          setHasGitHubAppInstalled(false);
          localStorage.setItem("github_app_installed", "false");
        }
      }
    } catch (error) {
      console.error("Error checking GitHub App installation:", error);
      setHasGitHubAppInstalled(false);
      localStorage.setItem("github_app_installed", "false");
    } finally {
      setIsCheckingAppInstallation(false);
    }
  };

  const handleLogin = () => {
    setIsLoading(true);
    window.location.href = "/api/auth/github/login";
  };

  const handleInstallGitHubApp = () => {
    setIsLoading(true);

    localStorage.removeItem("github_app_installed");
    window.location.href = "/api/github/installation";
  };

  if (!apiUrl || !assistantId) {
    return (
      <div className="flex min-h-screen w-full items-center justify-center p-4">
        <div className="animate-in fade-in-0 zoom-in-95 flex max-w-3xl flex-col rounded-lg border bg-red-50 shadow-lg">
          <div className="flex flex-col gap-4 border-b p-6">
            <div className="flex flex-col items-start gap-2">
              <LangGraphLogoSVG className="h-7" />
              <h1 className="text-xl font-semibold tracking-tight">
                Environment Variables Missing
              </h1>
            </div>
            <p className="text-muted-foreground">
              Whoops, looks like you don&apos;t have an API URL or assistant ID
              set in your environment variables. Please make sure you have both
              of these set before continuing.
            </p>
            <div className="relative">
              <TooltipIconButton
                onClick={() => {
                  const textToCopy = `NEXT_PUBLIC_API_URL=${apiUrl}\nNEXT_PUBLIC_ASSISTANT_ID=${assistantId}`;
                  navigator.clipboard.writeText(textToCopy).then(() => {
                    setCopyTooltipText("Copied!");
                    setTimeout(
                      () => setCopyTooltipText(baseCopyTooltipText),
                      2000,
                    );
                  });
                }}
                className="absolute top-2 right-2 cursor-pointer"
                tooltip={copyTooltipText}
              >
                {copyTooltipText === baseCopyTooltipText ? (
                  <motion.div
                    key="check"
                    initial={{ opacity: 0, scale: 0.8 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.8 }}
                    transition={{ duration: 0.15 }}
                  >
                    <Copy />
                  </motion.div>
                ) : (
                  <motion.div
                    key="copy"
                    initial={{ opacity: 0, scale: 0.8 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.8 }}
                    transition={{ duration: 0.15 }}
                  >
                    <CopyCheck className="text-green-500" />
                  </motion.div>
                )}
              </TooltipIconButton>
              <code className="bg-muted flex flex-col gap-2 rounded-md border border-red-200 px-4 py-3 text-sm">
                <span>NEXT_PUBLIC_API_URL={apiUrl}</span>
                <span>NEXT_PUBLIC_ASSISTANT_ID={assistantId}</span>
              </code>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (!isAuth) {
    return (
      <div className="flex min-h-screen w-full items-center justify-center p-4">
        <div className="animate-in fade-in-0 zoom-in-95 flex w-full max-w-3xl flex-col rounded-lg border shadow-lg">
          <div className="flex flex-col gap-4 border-b p-6">
            <div className="flex flex-col items-start gap-2">
              <LangGraphLogoSVG className="h-7" />
              <h1 className="text-xl font-semibold tracking-tight">
                Get started
              </h1>
            </div>
            <p className="text-muted-foreground">
              Connect your GitHub account to get started with Open SWE.
            </p>
            <Button
              onClick={handleLogin}
              disabled={isLoading}
            >
              <GitHubSVG
                width="16"
                height="16"
              />
              {isLoading ? "Connecting..." : "Connect GitHub"}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  // Step 2: GitHub App Installation (only show if authenticated but app not installed)
  // Only show modal if we've definitively determined the app is not installed
  // Don't show while we're still loading or have cached positive status
  if (isAuth && hasGitHubAppInstalled === false && !isTokenLoading) {
    return (
      <div className="flex min-h-screen w-full items-center justify-center p-4">
        <div className="animate-in fade-in-0 zoom-in-95 flex w-full max-w-3xl flex-col rounded-lg border shadow-lg">
          <div className="flex flex-col gap-4 border-b p-6">
            <div className="flex flex-col items-start gap-2">
              <LangGraphLogoSVG className="h-7" />
              <h1 className="text-xl font-semibold tracking-tight">
                One more step
              </h1>
            </div>
            <div className="text-muted-foreground flex items-center gap-2 text-sm">
              <span className="rounded-full bg-green-100 px-2 py-1 text-xs font-medium text-green-800">
                1. GitHub Login ✓
              </span>
              <ArrowRight className="h-3 w-3" />
              <span className="rounded-full bg-blue-100 px-2 py-1 text-xs font-medium text-blue-800">
                2. Repository Access
              </span>
            </div>
            <p className="text-muted-foreground">
              Great! Now we need access to your GitHub repositories. Install our
              GitHub App to grant access to specific repositories.
            </p>
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              <p>
                You'll be redirected to GitHub where you can select which
                repositories to grant access to.
              </p>
            </div>
            <Button
              onClick={handleInstallGitHubApp}
              disabled={isLoading || isCheckingAppInstallation}
              className="bg-black hover:bg-gray-800"
            >
              <GitHubSVG
                width="16"
                height="16"
              />
              {isLoading || isCheckingAppInstallation
                ? "Loading..."
                : "Install GitHub App"}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  // Only render StreamSession when we have a valid token
  if (!githubToken) {
    return (
      <div className="flex min-h-screen w-full items-center justify-center p-4">
        <div className="animate-in fade-in-0 zoom-in-95 flex w-full max-w-3xl flex-col rounded-lg border shadow-lg">
          <div className="flex flex-col gap-4 border-b p-6">
            <div className="flex flex-col items-start gap-2">
              <LangGraphLogoSVG className="h-7" />
              <h1 className="text-xl font-semibold tracking-tight">
                Loading...
              </h1>
            </div>
            <p className="text-muted-foreground">
              Setting up your GitHub integration...
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <StreamSession
      apiUrl={apiUrl}
      assistantId={assistantId}
      githubToken={githubToken}
    >
      {children}
    </StreamSession>
  );
};

export const useStreamContext = (): StreamContextType => {
  const context = useContext(StreamContext);
  if (context === undefined) {
    throw new Error("useStreamContext must be used within a StreamProvider");
  }
  return context;
};

export default StreamContext;
