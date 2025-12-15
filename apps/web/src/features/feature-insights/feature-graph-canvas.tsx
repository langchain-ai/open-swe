"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { v4 as uuidv4 } from "uuid";
import { Trash } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { FeatureEdge, FeatureNode } from "@openswe/shared/feature-graph/types";

type CanvasNode = {
  id: string;
  label: string;
  isEphemeral: boolean;
};

type PositionedNode = CanvasNode & {
  x: number;
  y: number;
};

type FeatureGraphCanvasProps = {
  features: FeatureNode[];
  edges: FeatureEdge[];
  selectedId: string | null;
  activeIds?: string[];
  onSelect?: (id: string | null) => void;
};

export function FeatureGraphCanvas({
  features,
  edges,
  selectedId,
  activeIds = [],
  onSelect,
}: FeatureGraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const [draft, setDraft] = useState("");
  const [ephemeralNodes, setEphemeralNodes] = useState<CanvasNode[]>([]);
  const [ephemeralSelection, setEphemeralSelection] = useState<string | null>(
    null,
  );

  useEffect(() => {
    if (!containerRef.current) return;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const { width, height } = entry.contentRect;
      setDimensions({ width, height });
    });

    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setEphemeralNodes([]);
    setEphemeralSelection(null);
  }, [features]);

  const allNodes: CanvasNode[] = useMemo(
    () => [
      ...features.map((feature) => ({
        id: feature.id,
        label: feature.name,
        isEphemeral: false,
      })),
      ...ephemeralNodes,
    ],
    [features, ephemeralNodes],
  );

  const positionedNodes = useMemo<PositionedNode[]>(() => {
    const { width, height } = dimensions;
    const count = allNodes.length || 1;
    const radius = Math.max(Math.min(width, height) / 2 - 40, 120);
    const centerX = width / 2 || 0;
    const centerY = height / 2 || 0;

    return allNodes.map((node, index) => {
      const angle = (index / count) * Math.PI * 2;
      const x = centerX + radius * Math.cos(angle);
      const y = centerY + radius * Math.sin(angle);

      return {
        ...node,
        x,
        y,
      };
    });
  }, [allNodes, dimensions]);

  const positionsById = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    for (const node of positionedNodes) {
      map.set(node.id, { x: node.x, y: node.y });
    }
    return map;
  }, [positionedNodes]);

  const visibleEdges = useMemo(
    () =>
      edges.filter(
        (edge) => positionsById.has(edge.source) && positionsById.has(edge.target),
      ),
    [edges, positionsById],
  );

  const resolvedSelection = useMemo(() => {
    if (ephemeralSelection) return ephemeralSelection;
    return selectedId ?? null;
  }, [ephemeralSelection, selectedId]);

  const selectedNode = useMemo(
    () => positionedNodes.find((node) => node.id === resolvedSelection),
    [positionedNodes, resolvedSelection],
  );

  const handleAddNode = () => {
    const label = draft.trim();
    if (!label) return;

    const newNode: CanvasNode = {
      id: `local-${uuidv4()}`,
      label,
      isEphemeral: true,
    };

    setEphemeralNodes((prev) => [...prev, newNode]);
    setEphemeralSelection(newNode.id);
    setDraft("");
  };

  const handleDeleteSelected = () => {
    if (!resolvedSelection) return;

    const node = positionedNodes.find((candidate) => candidate.id === resolvedSelection);
    if (!node || !node.isEphemeral) return;

    setEphemeralNodes((prev) => prev.filter((entry) => entry.id !== node.id));
    setEphemeralSelection(null);
  };

  const handleSelectNode = (node: CanvasNode) => {
    if (node.isEphemeral) {
      setEphemeralSelection(node.id);
      return;
    }

    setEphemeralSelection(null);
    onSelect?.(node.id);
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
        <Input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Add feature..."
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              handleAddNode();
            }
          }}
        />
        <div className="flex items-center gap-2">
          <Button onClick={handleAddNode} variant="secondary" size="sm">
            Add to graph
          </Button>
          {selectedNode?.isEphemeral && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleDeleteSelected}
              className="text-destructive hover:text-destructive"
            >
              <Trash className="mr-2 size-4" />
              Delete
            </Button>
          )}
        </div>
      </div>

      <div
        ref={containerRef}
        className="bg-muted/40 relative h-[460px] overflow-hidden rounded-lg border"
      >
        <svg className="absolute inset-0 h-full w-full" role="presentation">
          {visibleEdges.map((edge) => {
            const source = positionsById.get(edge.source)!;
            const target = positionsById.get(edge.target)!;

            return (
              <line
                key={`${edge.source}->${edge.target}`}
                x1={source.x}
                y1={source.y}
                x2={target.x}
                y2={target.y}
                strokeWidth={2}
                className="stroke-border/80"
              />
            );
          })}
        </svg>

        {positionedNodes.map((node) => {
          const isSelected = resolvedSelection === node.id;
          const isActive = activeIds.includes(node.id);
          return (
            <button
              key={node.id}
              type="button"
              className={cn(
                "border-border/60 bg-background/90 absolute flex max-w-[220px] -translate-x-1/2 -translate-y-1/2 cursor-pointer flex-col gap-2 rounded-xl border px-4 py-3 text-left shadow-sm transition",
                isSelected && "ring-2 ring-primary shadow-lg",
              )}
              style={{ left: node.x, top: node.y }}
              onClick={() => handleSelectNode(node)}
            >
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold leading-tight">{node.label}</span>
                {node.isEphemeral ? (
                  <Badge variant="secondary" className="text-[11px]">New</Badge>
                ) : isActive ? (
                  <Badge variant="outline" className="text-[11px]">Active</Badge>
                ) : null}
              </div>
              <p className="text-muted-foreground line-clamp-2 text-xs">
                {node.isEphemeral
                  ? "Local draft node"
                  : "Tap to open details and dependencies"}
              </p>
            </button>
          );
        })}
      </div>

      {resolvedSelection && selectedNode && (
        <div className="border-border/80 bg-background/80 flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm">
          <div className="flex flex-col gap-0.5">
            <span className="font-semibold">{selectedNode.label}</span>
            <span className="text-muted-foreground text-xs">
              {selectedNode.isEphemeral
                ? "Ephemeral node — not yet synced"
                : "From feature graph"}
            </span>
          </div>
          {selectedNode.isEphemeral && (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleDeleteSelected}
              className="text-destructive hover:text-destructive"
            >
              <Trash className="mr-2 size-4" />
              Delete
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

