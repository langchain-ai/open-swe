import { BranchSelector } from "./branch-selector";
import { RepositorySelector } from "./repository-selector";
import { useQueryState } from "nuqs";

export function RepositoryBranchSelectors() {
  const [threadId] = useQueryState("threadId");
  const chatStarted = !!threadId;
  const defaultButtonStyles =
    "bg-inherit border-none text-gray-500 hover:text-black dark:hover:text-gray-300 text-xs p-0 h-fit hover:bg-inherit";
  const defaultStylesChatStarted =
    "hover:bg-inherit cursor-default hover:cursor-default hover:text-black dark:hover:text-gray-300 hover:border-gray-300 hover:ring-inherit";

  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-0">
        <span className="-mr-2 text-gray-500">(</span>
        <RepositorySelector
          chatStarted={chatStarted}
          buttonClassName={
            defaultButtonStyles +
            (chatStarted ? " " + defaultStylesChatStarted : "")
          }
        />
        <span className="-ml-2 text-gray-500">)</span>
      </div>
      <div className="flex items-center gap-0">
        <span className="-mr-2 text-gray-500">(</span>
        <BranchSelector
          chatStarted={chatStarted}
          buttonClassName={
            defaultButtonStyles +
            (chatStarted ? " " + defaultStylesChatStarted : "")
          }
        />
        <span className="-ml-2 text-gray-500">)</span>
      </div>
    </div>
  );
}
