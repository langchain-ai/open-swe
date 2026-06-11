import type { Message, Project } from "@/lib/agents/types";

export interface ChangedFileSummaryItem {
  filePath: string;
  additions: number;
  deletions: number;
  originalContent: string;
  modifiedContent: string;
}

export interface ApprovalCallbacks {
  onApprove?: (approvalRequestId: string) => void;
  onReject?: (approvalRequestId: string) => void;
  onAutoApprove?: (approvalRequestId: string) => void;
  onOpenDiff?: (diffData: { filePath: string; originalContent: string; modifiedContent: string }) => void;
}

export type MessagesScrollControl = {
  scrollToBottom: () => void;
};

export interface MessagesProps extends ApprovalCallbacks {
  messages: Message[];
  isStreaming: boolean;
  /** Live run signal from `useStream().isLoading` — drives Streamdown token animation. */
  streamIsLoading?: boolean;
  /** When set, drives the thinking spinner (stream + pending). Falls back to streamIsLoading/isStreaming. */
  isThinking?: boolean;
  settingUpSandbox?: boolean;
  project?: Project | null;
  contentWidthClass?: string;
  /** Horizontal padding on centered content (scroll track stays edge-to-edge). */
  contentPaddingClass?: string;
  /** Extra scroll padding so content can scroll under a bottom overlay (e.g. floating prompt). */
  bottomInset?: number;
  /** When "external", parent renders the scroll button (e.g. above a floating prompt). */
  scrollButtonSlot?: "internal" | "external";
  onShowScrollToBottomChange?: (show: boolean) => void;
  scrollControlRef?: React.MutableRefObject<MessagesScrollControl | null>;
}
