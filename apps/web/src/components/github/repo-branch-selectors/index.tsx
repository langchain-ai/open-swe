import { BranchSelector } from "./branch-selector";
import { RepositorySelector } from "./repository-selector";

export function RepositoryBranchSelectors() {
  const defaultButtonStyles =
    "bg-inherit border-gray-300 rounded-full text-gray-500 hover:text-gray-700 text-xs";

  return (
    <div className="flex items-center gap-2">
      <RepositorySelector buttonClassName={defaultButtonStyles} />
      <BranchSelector buttonClassName={defaultButtonStyles} />
    </div>
  );
}
