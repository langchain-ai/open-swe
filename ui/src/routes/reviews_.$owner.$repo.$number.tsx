import { Link, Navigate, createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState } from "react";
import {
  ArrowClockwiseIcon,
  ArrowLeftIcon,
  BugBeetleIcon,
  CaretDownIcon,
  CheckCircleIcon,
  CheckIcon,
  CircleIcon,
  FlagIcon,
  GitPullRequestIcon,
  InfoIcon,
  XCircleIcon,
} from "@phosphor-icons/react";

import type {
  ReviewCheckRun,
  ReviewDetail,
  ReviewDiffFile,
  ReviewDiffLine,
  ReviewFinding,
  ReviewUserRef,
} from "@/lib/api";
import { Markdown } from "@/components/agents/ported";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";
import { prSizeLabel } from "@/routes/reviews";

export const Route = createFileRoute("/reviews_/$owner/$repo/$number")({
  component: ReviewDetailPage,
});

type CenterTab = "description" | "changes";
type SideTab = "info" | "chat";

interface FindingsByFile {
  byFile: Map<string, Array<ReviewFinding>>;
  unanchored: Array<ReviewFinding>;
}

function groupFindingsByFile(findings: Array<ReviewFinding>): FindingsByFile {
  const byFile = new Map<string, Array<ReviewFinding>>();
  const unanchored: Array<ReviewFinding> = [];
  for (const finding of findings) {
    if (finding.file && finding.in_diff && finding.end_line !== null) {
      const list = byFile.get(finding.file) ?? [];
      list.push(finding);
      byFile.set(finding.file, list);
    } else {
      unanchored.push(finding);
    }
  }
  return { byFile, unanchored };
}

const GROUP_STYLES = {
  bug: { label: "Bug", className: "text-destructive", Icon: BugBeetleIcon },
  investigate: { label: "Investigate", className: "text-amber-500", Icon: FlagIcon },
  informational: { label: "Informational", className: "text-muted-foreground", Icon: InfoIcon },
} as const;

function findingAnchorLabel(finding: ReviewFinding): string {
  if (finding.start_line === null || finding.end_line === null) return finding.file;
  if (finding.start_line === finding.end_line) return `${finding.file}:${finding.end_line}`;
  return `${finding.file}:${finding.start_line}-${finding.end_line}`;
}

function ReviewDetailPage() {
  const { owner, repo, number } = Route.useParams();
  const prNumber = Number(number);
  const session = useSession();
  const detail = useQuery({
    queryKey: ["review", owner, repo, prNumber],
    queryFn: () => api.getReview(owner, repo, prNumber),
    enabled: !!session.data && Number.isFinite(prNumber),
    refetchInterval: (query) => (query.state.data?.status === "running" ? 5000 : false),
  });
  const diff = useQuery({
    queryKey: ["reviewDiff", owner, repo, prNumber, detail.data?.head_sha],
    queryFn: () => api.getReviewDiff(owner, repo, prNumber),
    enabled: !!session.data && Number.isFinite(prNumber),
  });

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }
  if (!session.data) return <Navigate to="/login" />;

  return (
    <div className="flex h-svh flex-col overflow-hidden bg-background text-foreground">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border px-4 text-xs">
        <Link
          to="/reviews"
          className="inline-flex items-center gap-1.5 text-muted-foreground hover:text-foreground"
        >
          <ArrowLeftIcon className="size-3.5" />
          Reviews
        </Link>
        <span className="text-muted-foreground">/</span>
        <span className="inline-flex min-w-0 items-center gap-1.5 truncate">
          <GitPullRequestIcon className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate font-medium">
            {owner}/{repo}#{number}
            {detail.data ? ` ${detail.data.pr.title}` : ""}
          </span>
        </span>
        {detail.data && (
          <span className="ml-auto rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">
            {prSizeLabel(detail.data.pr.additions, detail.data.pr.deletions)}
          </span>
        )}
      </header>

      {detail.error ? (
        <div className="p-6 text-xs text-destructive">{detail.error.message}</div>
      ) : !detail.data ? (
        <div className="space-y-3 p-6">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      ) : (
        <ReviewBody detail={detail.data} diffFiles={diff.data?.files ?? null} />
      )}
    </div>
  );
}

function ReviewBody({
  detail,
  diffFiles,
}: {
  detail: ReviewDetail;
  diffFiles: Array<ReviewDiffFile> | null;
}) {
  const [centerTab, setCenterTab] = useState<CenterTab>("description");
  const [sideTab, setSideTab] = useState<SideTab>("info");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const fileRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const findingRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [expandedFiles, setExpandedFiles] = useState<Record<string, boolean>>({});

  const viewedStorageKey = `open-swe.review.viewed.${detail.owner}/${detail.repo}/${detail.number}.${detail.head_sha}`;
  const [viewed, setViewed] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      const raw = window.localStorage.getItem(viewedStorageKey);
      return new Set(raw ? (JSON.parse(raw) as Array<string>) : []);
    } catch {
      return new Set();
    }
  });
  const toggleViewed = useCallback(
    (path: string) => {
      setViewed((prev) => {
        const next = new Set(prev);
        if (next.has(path)) next.delete(path);
        else next.add(path);
        window.localStorage.setItem(viewedStorageKey, JSON.stringify(Array.from(next)));
        return next;
      });
    },
    [viewedStorageKey],
  );

  const findingsByFile = useMemo(() => groupFindingsByFile(detail.findings), [detail.findings]);

  const linesLeft = useMemo(() => {
    if (!diffFiles) return null;
    return diffFiles
      .filter((file) => !viewed.has(file.path))
      .reduce((acc, file) => acc + file.additions + file.deletions, 0);
  }, [diffFiles, viewed]);

  const scrollToFile = useCallback((path: string) => {
    setCenterTab("changes");
    setSelectedFile(path);
    setExpandedFiles((prev) => ({ ...prev, [path]: true }));
    requestAnimationFrame(() => {
      fileRefs.current[path]?.scrollIntoView({ block: "start", behavior: "smooth" });
    });
  }, []);

  const scrollToFinding = useCallback(
    (finding: ReviewFinding) => {
      if (!finding.file || !finding.in_diff || finding.end_line === null) return;
      setCenterTab("changes");
      setExpandedFiles((prev) => ({ ...prev, [finding.file]: true }));
      requestAnimationFrame(() => {
        const node = findingRefs.current[finding.id] ?? fileRefs.current[finding.file];
        node?.scrollIntoView({ block: "center", behavior: "smooth" });
      });
    },
    [],
  );

  return (
    <div className="flex min-h-0 flex-1">
      <FileTreeSidebar
        files={diffFiles}
        selected={selectedFile}
        viewed={viewed}
        onSelect={scrollToFile}
      />

      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-4xl px-6 py-6">
          <PrHeader detail={detail} centerTab={centerTab} onTabChange={setCenterTab} />
          {centerTab === "description" ? (
            <div className="mt-4 rounded-lg border border-border bg-card p-4">
              {detail.pr.body ? (
                <Markdown content={detail.pr.body} />
              ) : (
                <p className="text-xs text-muted-foreground">This PR has no description.</p>
              )}
            </div>
          ) : null}

          <div className="mt-6">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-medium">Changes</h2>
              {linesLeft !== null && (
                <span className="text-xs text-muted-foreground">
                  {linesLeft === 0 ? "All lines reviewed" : `${linesLeft} lines left`}
                </span>
              )}
            </div>
            {!diffFiles ? (
              <Skeleton className="h-64 w-full" />
            ) : diffFiles.length === 0 ? (
              <p className="text-xs text-muted-foreground">No diff available.</p>
            ) : (
              <div className="space-y-3">
                {diffFiles.map((file) => (
                  <FileDiffCard
                    key={file.path}
                    file={file}
                    findings={findingsByFile.byFile.get(file.path) ?? []}
                    viewed={viewed.has(file.path)}
                    onToggleViewed={() => toggleViewed(file.path)}
                    expanded={expandedFiles[file.path] ?? !viewed.has(file.path)}
                    onToggleExpanded={() =>
                      setExpandedFiles((prev) => ({
                        ...prev,
                        [file.path]: !(prev[file.path] ?? !viewed.has(file.path)),
                      }))
                    }
                    sectionRef={(node) => {
                      fileRefs.current[file.path] = node;
                    }}
                    findingRef={(id, node) => {
                      findingRefs.current[id] = node;
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </main>

      <SidePanel
        detail={detail}
        tab={sideTab}
        onTabChange={setSideTab}
        onFindingClick={scrollToFinding}
      />
    </div>
  );
}

function PrHeader({
  detail,
  centerTab,
  onTabChange,
}: {
  detail: ReviewDetail;
  centerTab: CenterTab;
  onTabChange: (tab: CenterTab) => void;
}) {
  const { pr } = detail;
  const stateStyles: Record<string, string> = {
    open: "border-emerald-600/40 text-emerald-500",
    draft: "border-border text-muted-foreground",
    merged: "border-purple-600/40 text-purple-500",
    closed: "border-red-600/40 text-red-500",
  };
  return (
    <div>
      <span
        className={cn(
          "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] capitalize",
          stateStyles[pr.state] ?? stateStyles.open,
        )}
      >
        <GitPullRequestIcon className="size-3" />
        {pr.state}
      </span>
      <h1 className="mt-2 text-lg font-medium">
        <a href={detail.url} target="_blank" rel="noreferrer" className="hover:underline">
          {pr.title}
        </a>
      </h1>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {pr.author && <span className="font-medium text-foreground">{pr.author.login}</span>}
        <span className="rounded border border-border px-1.5 py-0.5 font-mono text-[11px]">
          {pr.base_ref}
        </span>
        <span>←</span>
        <span className="rounded border border-border px-1.5 py-0.5 font-mono text-[11px]">
          {pr.head_ref}
        </span>
        <span>
          {pr.changed_files} file{pr.changed_files === 1 ? "" : "s"}
        </span>
        <span className="text-emerald-500">+{pr.additions}</span>
        <span className="text-red-500">-{pr.deletions}</span>
      </div>
      <div className="mt-4 flex items-center gap-1 border-b border-border">
        {(
          [
            ["description", "Description"],
            ["changes", "Changes"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => onTabChange(id)}
            className={cn(
              "border-b-2 px-3 py-2 text-xs transition-colors",
              centerTab === id
                ? "border-foreground font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

function FileTreeSidebar({
  files,
  selected,
  viewed,
  onSelect,
}: {
  files: Array<ReviewDiffFile> | null;
  selected: string | null;
  viewed: Set<string>;
  onSelect: (path: string) => void;
}) {
  const grouped = useMemo(() => {
    const byDir = new Map<string, Array<ReviewDiffFile>>();
    for (const file of files ?? []) {
      const idx = file.path.lastIndexOf("/");
      const dir = idx === -1 ? "" : file.path.slice(0, idx);
      const list = byDir.get(dir) ?? [];
      list.push(file);
      byDir.set(dir, list);
    }
    return Array.from(byDir.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [files]);

  return (
    <aside className="hidden w-64 shrink-0 overflow-y-auto border-r border-border py-3 lg:block">
      {!files ? (
        <div className="px-3">
          <Skeleton className="h-40 w-full" />
        </div>
      ) : (
        grouped.map(([dir, dirFiles]) => (
          <div key={dir || "."} className="mb-2">
            {dir && (
              <div className="truncate px-3 py-1 text-[11px] text-muted-foreground">{dir}</div>
            )}
            {dirFiles.map((file) => {
              const name = file.path.slice(dir ? dir.length + 1 : 0);
              return (
                <button
                  key={file.path}
                  type="button"
                  onClick={() => onSelect(file.path)}
                  className={cn(
                    "flex w-full items-center gap-2 px-3 py-1 text-left text-xs transition-colors hover:bg-muted/40",
                    selected === file.path && "bg-muted/60",
                    viewed.has(file.path) && "opacity-50",
                  )}
                >
                  <span
                    className={cn(
                      "truncate",
                      file.status === "added" && "text-emerald-500",
                      file.status === "deleted" && "text-red-500",
                    )}
                  >
                    {name}
                  </span>
                  <span className="ml-auto flex shrink-0 items-center gap-1.5 font-mono text-[10px]">
                    {file.additions > 0 && (
                      <span className="text-emerald-500">+{file.additions}</span>
                    )}
                    {file.deletions > 0 && <span className="text-red-500">-{file.deletions}</span>}
                  </span>
                </button>
              );
            })}
          </div>
        ))
      )}
    </aside>
  );
}

function FileDiffCard({
  file,
  findings,
  viewed,
  onToggleViewed,
  expanded,
  onToggleExpanded,
  sectionRef,
  findingRef,
}: {
  file: ReviewDiffFile;
  findings: Array<ReviewFinding>;
  viewed: boolean;
  onToggleViewed: () => void;
  expanded: boolean;
  onToggleExpanded: () => void;
  sectionRef: (node: HTMLDivElement | null) => void;
  findingRef: (id: string, node: HTMLDivElement | null) => void;
}) {
  const findingsByLine = useMemo(() => {
    const map = new Map<string, Array<ReviewFinding>>();
    for (const finding of findings) {
      if (finding.end_line === null) continue;
      const key = `${finding.side}:${finding.end_line}`;
      const list = map.get(key) ?? [];
      list.push(finding);
      map.set(key, list);
    }
    return map;
  }, [findings]);

  return (
    <div
      ref={sectionRef}
      className="scroll-mt-4 overflow-hidden rounded-lg border border-border"
    >
      <div className="flex items-center gap-2 bg-muted/40 px-3 py-2 text-xs">
        <button
          type="button"
          onClick={onToggleExpanded}
          className="inline-flex items-center gap-2 text-left"
        >
          <CaretDownIcon
            className={cn("size-3 transition-transform", !expanded && "-rotate-90")}
          />
          <span className="font-mono font-medium">{file.path}</span>
        </button>
        <span className="flex items-center gap-1.5 font-mono text-[11px]">
          <span className="text-emerald-500">+{file.additions}</span>
          <span className="text-red-500">-{file.deletions}</span>
        </span>
        {findings.length > 0 && (
          <span className="inline-flex items-center gap-1 text-[11px] text-amber-500">
            <FlagIcon className="size-3" />
            {findings.length}
          </span>
        )}
        <label className="ml-auto inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground">
          Mark as viewed
          <button
            type="button"
            role="checkbox"
            aria-checked={viewed}
            onClick={onToggleViewed}
            className={cn(
              "flex size-4 items-center justify-center rounded border border-border",
              viewed && "bg-foreground text-background",
            )}
          >
            {viewed && <CheckIcon className="size-3" />}
          </button>
        </label>
      </div>
      {expanded && (
        <div className="overflow-x-auto bg-card font-mono text-[11px] leading-5">
          {file.hunks.map((hunk, hunkIndex) => (
            <div key={hunkIndex}>
              <div className="bg-muted/60 px-3 py-1 text-muted-foreground">{hunk.header}</div>
              {hunk.lines.map((line, lineIndex) => (
                <DiffLineRow
                  key={lineIndex}
                  line={line}
                  findings={
                    findingsByLine.get(
                      line.kind === "del"
                        ? `LEFT:${line.old_line}`
                        : `RIGHT:${line.new_line}`,
                    ) ?? []
                  }
                  findingRef={findingRef}
                />
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DiffLineRow({
  line,
  findings,
  findingRef,
}: {
  line: ReviewDiffLine;
  findings: Array<ReviewFinding>;
  findingRef: (id: string, node: HTMLDivElement | null) => void;
}) {
  return (
    <>
      <div
        className={cn(
          "flex",
          line.kind === "add" && "bg-emerald-500/10",
          line.kind === "del" && "bg-red-500/10",
        )}
      >
        <span className="w-10 shrink-0 select-none px-1 text-right text-muted-foreground/60">
          {line.old_line ?? ""}
        </span>
        <span className="w-10 shrink-0 select-none px-1 text-right text-muted-foreground/60">
          {line.new_line ?? ""}
        </span>
        <span
          className={cn(
            "w-4 shrink-0 select-none text-center",
            line.kind === "add" && "text-emerald-500",
            line.kind === "del" && "text-red-500",
          )}
        >
          {line.kind === "add" ? "+" : line.kind === "del" ? "-" : ""}
        </span>
        <span className="whitespace-pre pr-3">{line.text}</span>
      </div>
      {findings.map((finding) => (
        <div
          key={finding.id}
          ref={(node) => findingRef(finding.id, node)}
          className="border-y border-border bg-background px-3 py-2 font-sans"
        >
          <InlineFindingCard finding={finding} />
        </div>
      ))}
    </>
  );
}

function InlineFindingCard({ finding }: { finding: ReviewFinding }) {
  const style = GROUP_STYLES[finding.group];
  const Icon = style.Icon;
  return (
    <div className={cn(finding.status !== "open" && "opacity-60")}>
      <div className="flex items-center gap-2 text-xs">
        <Icon className={cn("size-3.5", style.className)} />
        <span className={cn("font-medium", style.className)}>{style.label}</span>
        <span className="font-medium text-foreground">{finding.title}</span>
        {finding.outdated && <Badgeish>Outdated</Badgeish>}
        {finding.status !== "open" && <Badgeish>{finding.status}</Badgeish>}
      </div>
      <div className="mt-1 text-xs text-muted-foreground">
        <Markdown content={finding.description} />
      </div>
      {finding.suggestion && (
        <pre className="mt-2 overflow-x-auto rounded border border-border bg-muted/40 p-2 font-mono text-[11px]">
          {finding.suggestion}
        </pre>
      )}
    </div>
  );
}

function Badgeish({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded border border-border px-1.5 py-0.5 text-[10px] capitalize text-muted-foreground">
      {children}
    </span>
  );
}

function SidePanel({
  detail,
  tab,
  onTabChange,
  onFindingClick,
}: {
  detail: ReviewDetail;
  tab: SideTab;
  onTabChange: (tab: SideTab) => void;
  onFindingClick: (finding: ReviewFinding) => void;
}) {
  const qc = useQueryClient();
  const reReview = useMutation({
    mutationFn: () => api.reReview(detail.owner, detail.repo, detail.number),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["review", detail.owner, detail.repo, detail.number] });
    },
  });

  const readStorageKey = `open-swe.review.read.${detail.thread_id}`;
  const [read, setRead] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      const raw = window.localStorage.getItem(readStorageKey);
      return new Set(raw ? (JSON.parse(raw) as Array<string>) : []);
    } catch {
      return new Set();
    }
  });
  const markAllRead = () => {
    const next = new Set(detail.findings.map((f) => f.id));
    setRead(next);
    window.localStorage.setItem(readStorageKey, JSON.stringify(Array.from(next)));
  };

  const bugs = detail.findings.filter((f) => f.group === "bug");
  const flags = detail.findings.filter((f) => f.group !== "bug");
  const openBugs = bugs.filter((f) => f.status === "open");

  return (
    <aside className="hidden w-80 shrink-0 flex-col overflow-y-auto border-l border-border xl:flex">
      <div className="flex items-center gap-1 border-b border-border px-3 py-2">
        {(
          [
            ["info", "Info"],
            ["chat", "Chat"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => onTabChange(id)}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs transition-colors",
              tab === id
                ? "bg-muted font-medium text-foreground"
                : "text-muted-foreground hover:bg-muted/50",
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "chat" ? (
        <div className="flex flex-1 items-center justify-center p-6 text-xs text-muted-foreground">
          Coming Soon
        </div>
      ) : (
        <div className="divide-y divide-border">
          <section className="px-3 py-3">
            <div className="flex items-center justify-between text-xs">
              <span className="font-medium">
                {detail.status === "running"
                  ? "PR analysis in progress"
                  : detail.status === "error"
                    ? "PR analysis failed"
                    : "PR analysis complete"}
              </span>
              <button
                type="button"
                onClick={() => reReview.mutate()}
                disabled={reReview.isPending || detail.status === "running"}
                className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground disabled:opacity-50"
              >
                <ArrowClockwiseIcon className="size-3" />
                Re-review
              </button>
            </div>
            <div className="mt-2 space-y-1 text-[11px] text-muted-foreground">
              <div>Reviewing commit {detail.head_sha.slice(0, 7) || "—"}</div>
              {detail.watch && <div>Watching for new pushes</div>}
              {reReview.error && (
                <div className="text-destructive">{reReview.error.message}</div>
              )}
            </div>
          </section>

          <section className="px-3 py-3">
            <div className="mb-2 flex items-center justify-between text-xs">
              <span className="inline-flex items-center gap-1.5 font-medium">
                <BugBeetleIcon className="size-3.5" />
                {openBugs.length === 0 ? "0 Bugs" : `${openBugs.length} Bugs`}
              </span>
            </div>
            <FindingList findings={bugs} read={read} onFindingClick={onFindingClick} />
          </section>

          <section className="px-3 py-3">
            <div className="mb-2 flex items-center justify-between text-xs">
              <span className="inline-flex items-center gap-1.5 font-medium">
                <FlagIcon className="size-3.5" />
                {flags.filter((f) => f.status === "open").length} Flags
              </span>
              {detail.findings.length > 0 && (
                <button
                  type="button"
                  onClick={markAllRead}
                  className="rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground"
                >
                  Mark all as read
                </button>
              )}
            </div>
            <FindingList findings={flags} read={read} onFindingClick={onFindingClick} />
          </section>

          <ChecksSection checks={detail.checks} />
          <PeopleSection title="Reviewers" people={detail.pr.requested_reviewers} />
          <PeopleSection title="Assignees" people={detail.pr.assignees} />
          <section className="px-3 py-3">
            <h3 className="mb-2 text-xs font-medium">Labels</h3>
            {detail.pr.labels.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">None</p>
            ) : (
              <div className="flex flex-wrap gap-1">
                {detail.pr.labels.map((label) => (
                  <span
                    key={label.name}
                    className="rounded-full border border-border px-2 py-0.5 text-[11px]"
                  >
                    {label.name}
                  </span>
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </aside>
  );
}

function FindingList({
  findings,
  read,
  onFindingClick,
}: {
  findings: Array<ReviewFinding>;
  read: Set<string>;
  onFindingClick: (finding: ReviewFinding) => void;
}) {
  if (findings.length === 0) {
    return <p className="text-[11px] text-muted-foreground">No issues found.</p>;
  }
  return (
    <div className="space-y-1.5">
      {findings.map((finding) => {
        const style = GROUP_STYLES[finding.group];
        const Icon = style.Icon;
        const muted = finding.status !== "open" || read.has(finding.id);
        return (
          <button
            key={finding.id}
            type="button"
            onClick={() => onFindingClick(finding)}
            className={cn(
              "block w-full rounded-md border border-transparent px-2 py-1.5 text-left transition-colors hover:border-border hover:bg-muted/40",
              muted && "opacity-50",
            )}
          >
            <span className="flex items-start gap-1.5 text-xs">
              <Icon className={cn("mt-0.5 size-3.5 shrink-0", style.className)} />
              <span className="min-w-0">
                <span className="line-clamp-2 font-medium text-foreground">
                  {finding.title || finding.description}
                </span>
                <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                  <span className={style.className}>{style.label}</span>
                  <span className="truncate font-mono">{findingAnchorLabel(finding)}</span>
                  {finding.outdated && <Badgeish>Outdated</Badgeish>}
                  {finding.status !== "open" && <Badgeish>{finding.status}</Badgeish>}
                </span>
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

function ChecksSection({ checks }: { checks: Array<ReviewCheckRun> }) {
  return (
    <section className="px-3 py-3">
      <h3 className="mb-2 text-xs font-medium">Checks</h3>
      {checks.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No checks reported.</p>
      ) : (
        <div className="space-y-1">
          {checks.map((check, index) => (
            <a
              key={`${check.name}-${index}`}
              href={check.url ?? undefined}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground"
            >
              <CheckStatusIcon check={check} />
              <span className="truncate">{check.name}</span>
            </a>
          ))}
        </div>
      )}
    </section>
  );
}

function CheckStatusIcon({ check }: { check: ReviewCheckRun }) {
  if (check.status !== "completed") {
    return <CircleIcon className="size-3.5 shrink-0 animate-pulse text-amber-500" />;
  }
  if (check.conclusion === "success" || check.conclusion === "neutral") {
    return <CheckCircleIcon className="size-3.5 shrink-0 text-emerald-500" />;
  }
  if (check.conclusion === "skipped") {
    return <CircleIcon className="size-3.5 shrink-0 text-muted-foreground" />;
  }
  return <XCircleIcon className="size-3.5 shrink-0 text-red-500" />;
}

function PeopleSection({ title, people }: { title: string; people: Array<ReviewUserRef> }) {
  return (
    <section className="px-3 py-3">
      <h3 className="mb-2 text-xs font-medium">{title}</h3>
      {people.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">None</p>
      ) : (
        <div className="space-y-1">
          {people.map((person) => (
            <div key={person.login} className="flex items-center gap-2 text-[11px]">
              {person.avatar_url ? (
                <img src={person.avatar_url} alt="" className="size-4 rounded-full" />
              ) : (
                <span className="size-4 rounded-full bg-muted" />
              )}
              {person.login}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
