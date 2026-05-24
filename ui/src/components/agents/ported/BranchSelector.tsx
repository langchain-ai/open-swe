import { useState, useRef, useCallback, useMemo } from 'react';

interface BranchSelectorProps {
  projectPath: string;
  currentBranch: string;
}

export function BranchSelector({ projectPath, currentBranch }: BranchSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [branches, setBranches] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [newBranchName, setNewBranchName] = useState('');
  const [showNewBranchInput, setShowNewBranchInput] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const filteredBranches = useMemo(() => {
    if (!searchQuery.trim()) return branches;
    const query = searchQuery.toLowerCase();
    return branches.filter((branch) => branch.toLowerCase().includes(query));
  }, [branches, searchQuery]);

  const loadBranches = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await window.git.listBranches(projectPath);
      setBranches(result.branches);
    } catch {
      setError('Failed to load branches');
    } finally {
      setLoading(false);
    }
  }, [projectPath]);

  const handleOpen = useCallback(() => {
    setIsOpen(true);
    setShowNewBranchInput(false);
    setNewBranchName('');
    setSearchQuery('');
    setError(null);
    loadBranches();
    setTimeout(() => searchInputRef.current?.focus(), 0);
  }, [loadBranches]);

  const handleClose = useCallback(() => {
    setIsOpen(false);
    setShowNewBranchInput(false);
    setNewBranchName('');
    setSearchQuery('');
    setError(null);
  }, []);

  const handleSwitchBranch = useCallback(async (branchName: string) => {
    if (branchName === currentBranch) {
      handleClose();
      return;
    }
    setLoading(true);
    setError(null);
    const result = await window.git.switchBranch(projectPath, branchName);
    setLoading(false);
    if (result.success) {
      handleClose();
    } else {
      setError(result.error || 'Failed to switch branch');
    }
  }, [projectPath, currentBranch, handleClose]);

  const handleCreateBranch = useCallback(async () => {
    const name = newBranchName.trim();
    if (!name) return;
    setLoading(true);
    setError(null);
    const result = await window.git.createBranch(projectPath, name);
    setLoading(false);
    if (result.success) {
      handleClose();
    } else {
      setError(result.error || 'Failed to create branch');
    }
  }, [projectPath, newBranchName, handleClose]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      handleClose();
    } else if (e.key === 'Enter' && showNewBranchInput) {
      handleCreateBranch();
    }
  }, [handleClose, showNewBranchInput, handleCreateBranch]);

  return (
    <div className="relative" onKeyDown={handleKeyDown}>
      <button
        onClick={isOpen ? handleClose : handleOpen}
        className="text-[#5a9bc7] hover:text-[#7ab8de] transition-colors cursor-pointer truncate block max-w-full"
      >
        {currentBranch}
      </button>

      {isOpen && (
        <>
          <div className="fixed inset-0 z-40" onClick={handleClose} />
          <div
            ref={dropdownRef}
            className="absolute top-full left-1/2 -translate-x-1/2 mt-2 w-80 bg-[#1a2332] border border-gray-700 rounded-lg shadow-xl z-50 overflow-hidden"
          >
            <div className="p-2 border-b border-gray-700">
              <input
                ref={searchInputRef}
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search branches..."
                className="w-full px-2 py-1.5 text-xs bg-gray-800 border border-gray-600 rounded text-gray-200 placeholder-gray-500 focus:outline-none focus:border-[#5a9bc7]"
              />
            </div>

            {error && (
              <div className="px-3 py-1.5 text-[11px] text-red-400 bg-red-900/20">
                {error}
              </div>
            )}

            <div className="max-h-48 overflow-y-auto">
              {loading && branches.length === 0 ? (
                <div className="px-3 py-1.5 text-xs text-gray-500">Loading...</div>
              ) : filteredBranches.length === 0 ? (
                <div className="px-3 py-1.5 text-xs text-gray-500">No branches found</div>
              ) : (
                filteredBranches.map((branch) => (
                  <button
                    key={branch}
                    onClick={() => handleSwitchBranch(branch)}
                    disabled={loading}
                    className={`w-full px-3 py-1.5 text-left text-xs transition-colors ${
                      branch === currentBranch
                        ? 'bg-[#5a9bc7]/20 text-[#5a9bc7]'
                        : 'text-gray-300 hover:bg-gray-800'
                    } ${loading ? 'opacity-50' : ''}`}
                  >
                    {branch}
                    {branch === currentBranch && (
                      <span className="ml-2 text-[10px] text-gray-500">(current)</span>
                    )}
                  </button>
                ))
              )}
            </div>

            <div className="border-t border-gray-700 p-2">
              {showNewBranchInput ? (
                <div className="flex gap-2">
                  <input
                    ref={inputRef}
                    type="text"
                    value={newBranchName}
                    onChange={(e) => setNewBranchName(e.target.value)}
                    placeholder="Branch name"
                    autoFocus
                    className="flex-1 px-2 py-1 text-xs bg-gray-800 border border-gray-600 rounded text-gray-200 placeholder-gray-500 focus:outline-none focus:border-[#5a9bc7]"
                  />
                  <button
                    onClick={handleCreateBranch}
                    disabled={loading || !newBranchName.trim()}
                    className="px-2 py-1 text-xs bg-[#5a9bc7] text-white rounded hover:bg-[#4a8bb7] disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Create
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setShowNewBranchInput(true)}
                  className="w-full px-3 py-1.5 text-left text-xs text-gray-400 hover:text-gray-200 hover:bg-gray-800 rounded transition-colors"
                >
                  + Create new branch
                </button>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
