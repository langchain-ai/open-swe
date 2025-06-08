import { BranchSelector } from "./branch-selector";
import { RepositorySelector } from "./repository-selector";
import { useQueryState } from "nuqs";

export function RepositoryBranchSelectors() {
  const [threadId] = useQueryState("threadId");
  const chatStarted = !!threadId;
  const defaultButtonStyles =
    "bg-inherit border-gray-300 rounded-full text-gray-500 hover:text-gray-700 text-xs";
  const defaultStylesChatStarted =
    "hover:bg-inherit cursor-default hover:cursor-default hover:text-gray-500 hover:border-gray-300 hover:ring-inherit";

  return (
    <div className="flex items-center gap-2">
      <RepositorySelector
        chatStarted={chatStarted}
        buttonClassName={
          defaultButtonStyles +
          (chatStarted ? " " + defaultStylesChatStarted : "")
        }
      />
      <BranchSelector
        chatStarted={chatStarted}
        buttonClassName={
          defaultButtonStyles +
          (chatStarted ? " " + defaultStylesChatStarted : "")
        }
      />
    </div>
  );
}
