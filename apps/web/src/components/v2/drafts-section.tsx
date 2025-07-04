"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Button } from "../ui/button";
import { Trash2, FileText, Archive } from "lucide-react";
import { useDraftStorage } from "@/hooks/useDraftStorage";

interface Draft {
  id: string;
  content: string;
  timestamp: number;
}

interface DraftsSectionProps {
  onLoadDraft: (content: string) => void;
}

export function DraftsSection({ onLoadDraft }: DraftsSectionProps) {
  const { getAllDrafts, deleteDraft } = useDraftStorage();
  const [drafts, setDrafts] = useState<Draft[]>([]);

  // Load drafts on mount and refresh when needed
  useEffect(() => {
    const loadDrafts = () => {
      const allDrafts = getAllDrafts();
      setDrafts(allDrafts);
    };

    loadDrafts();

    // Set up interval to refresh drafts periodically
    const interval = setInterval(loadDrafts, 1000);

    return () => clearInterval(interval);
  }, [getAllDrafts]);

  const handleLoadDraft = (draft: Draft) => {
    onLoadDraft(draft.content);
  };

  const handleDeleteDraft = (draftId: string, event: React.MouseEvent) => {
    event.stopPropagation();
    deleteDraft(draftId);
    // Refresh drafts list
    const updatedDrafts = getAllDrafts();
    setDrafts(updatedDrafts);
  };

  const formatTimestamp = (timestamp: number) => {
    const date = new Date(timestamp);
    const now = new Date();
    const diffInMinutes = Math.floor(
      (now.getTime() - date.getTime()) / (1000 * 60),
    );

    if (diffInMinutes < 1) {
      return "Just now";
    } else if (diffInMinutes < 60) {
      return `${diffInMinutes}m ago`;
    } else if (diffInMinutes < 1440) {
      const hours = Math.floor(diffInMinutes / 60);
      return `${hours}h ago`;
    } else {
      const days = Math.floor(diffInMinutes / 1440);
      return `${days}d ago`;
    }
  };

  const truncateContent = (content: string, maxLength: number = 100) => {
    if (content.length <= maxLength) return content;
    return content.substring(0, maxLength) + "...";
  };

  if (drafts.length === 0) {
    return null;
  }

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-foreground text-base font-semibold">
          Saved Drafts
        </h2>
        <span className="text-muted-foreground text-xs">
          {drafts.length} draft{drafts.length !== 1 ? "s" : ""}
        </span>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        {drafts.map((draft) => (
          <Card
            key={draft.id}
            className="border-border bg-card hover:bg-muted cursor-pointer px-0 py-3 transition-shadow hover:shadow-lg dark:bg-gray-950"
            onClick={() => handleLoadDraft(draft)}
          >
            <CardHeader>
              <div className="flex items-start justify-between">
                <div className="min-w-0 flex-1">
                  <CardTitle className="text-foreground truncate text-sm font-medium">
                    {truncateContent(draft.content, 60)}
                  </CardTitle>
                  <div className="mt-1 flex items-center gap-1">
                    <FileText className="text-muted-foreground h-3 w-3" />
                    <span className="text-muted-foreground text-xs">Draft</span>
                  </div>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground text-xs">
                  {formatTimestamp(draft.timestamp)}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-muted-foreground h-5 w-5 p-0 hover:text-red-500"
                  onClick={(e) => handleDeleteDraft(draft.id, e)}
                >
                  <Trash2 className="h-3 w-3" />
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
