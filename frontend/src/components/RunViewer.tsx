import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, ChunkMeta, RunDetail, RunSummary } from "../lib/api";
import { commitWebUrl, formatDate, timeAgo } from "../lib/format";

type Props = {
  slug: string;
  repoUrl: string;
  runs: RunSummary[];
  initialRunId: string | null;
  onDeleted?: () => void;
};

export function RunViewer({ slug, repoUrl, runs, initialRunId, onDeleted }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(initialRunId);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [activeChunk, setActiveChunk] = useState<number | null>(null);
  const [view, setView] = useState<"final" | "chunk">("final");

  useEffect(() => {
    if (runs.length === 0) {
      setSelectedId(null);
      setDetail(null);
      return;
    }
    if (!selectedId || !runs.find((r) => r.run_id === selectedId)) {
      setSelectedId(runs[0].run_id);
    }
  }, [runs, selectedId]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setErr(null);
    api
      .getRun(slug, selectedId)
      .then((d) => {
        if (!cancelled) {
          setDetail(d);
          setActiveChunk(null);
          setView("final");
        }
      })
      .catch((e) => !cancelled && setErr(e.message || String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [slug, selectedId]);

  const currentRun = useMemo(
    () => runs.find((r) => r.run_id === selectedId) || null,
    [runs, selectedId]
  );

  if (runs.length === 0) {
    return (
      <div className="card p-8 text-center text-ink-400">
        <div className="mb-2 text-3xl">🌊</div>
        <div className="text-base font-medium text-ink-200">No analyses yet</div>
        <div className="mt-1 text-sm">
          Hit{" "}
          <span className="font-mono text-accent-200">
            Analyze recent changes
          </span>{" "}
          above to generate your first digest.
        </div>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700/60 bg-ink-900/50 px-5 py-3">
        <div>
          <div className="text-sm font-semibold text-white">Analysis viewer</div>
          <div className="text-xs text-ink-400">
            {runs.length} {runs.length === 1 ? "run" : "runs"} on disk
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="label">Snapshot:</label>
          <select
            className="input max-w-xs"
            value={selectedId || ""}
            onChange={(e) => setSelectedId(e.target.value)}
          >
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.run_id} · {r.commit_count} commits · {timeAgo(r.timestamp)}
              </option>
            ))}
          </select>
          {currentRun && (
            <button
              className="btn-ghost text-xs"
              onClick={async () => {
                if (!confirm(`Delete run ${currentRun.run_id}?`)) return;
                await api.deleteRun(slug, currentRun.run_id);
                onDeleted?.();
              }}
            >
              Delete
            </button>
          )}
        </div>
      </div>

      {loading && (
        <div className="p-6 text-sm text-ink-400">Loading run…</div>
      )}
      {err && <div className="p-6 text-sm text-red-300">Failed: {err}</div>}

      {detail && currentRun && (
        <div className="grid grid-cols-1 md:grid-cols-[300px,1fr]">
          <div className="border-b border-ink-700/60 md:border-b-0 md:border-r">
            <div className="px-5 py-4">
              <div className="label">Run details</div>
              <div className="mt-2 space-y-1.5 text-xs text-ink-300">
                <div>
                  <span className="text-ink-500">Started:</span>{" "}
                  {formatDate(currentRun.timestamp)}
                </div>
                <div>
                  <span className="text-ink-500">Model:</span>{" "}
                  <span className="font-mono">{currentRun.model}</span>
                </div>
                <div>
                  <span className="text-ink-500">Window:</span>{" "}
                  {currentRun.since || "since-last"}
                </div>
                <div>
                  <span className="text-ink-500">Commits:</span>{" "}
                  {currentRun.commit_count}
                </div>
                <div>
                  <span className="text-ink-500">Chunks:</span>{" "}
                  {currentRun.chunk_count}
                  {currentRun.failed_chunks > 0 && (
                    <span className="ml-2 text-amber-300">
                      ({currentRun.failed_chunks} failed)
                    </span>
                  )}
                </div>
                {currentRun.first_commit && (
                  <div>
                    <span className="text-ink-500">Range:</span>{" "}
                    <span className="font-mono">
                      {currentRun.first_commit.short}..
                      {currentRun.last_commit?.short || ""}
                    </span>
                  </div>
                )}
              </div>
            </div>

            <div className="border-t border-ink-700/60 px-3 py-3">
              <button
                onClick={() => {
                  setView("final");
                  setActiveChunk(null);
                }}
                className={`block w-full rounded-lg px-3 py-2 text-left text-sm transition ${
                  view === "final"
                    ? "bg-accent-500/10 text-white ring-1 ring-accent-500/40"
                    : "text-ink-200 hover:bg-ink-700/40"
                }`}
              >
                <div className="font-medium">Final synthesis</div>
                <div className="text-[11px] text-ink-500">
                  Reduce-pass narrative
                </div>
              </button>

              <div className="mt-3 px-1 text-[10px] uppercase tracking-widest text-ink-500">
                Chunks ({detail.maps.length})
              </div>
              <ul className="mt-1 max-h-80 overflow-y-auto pr-1">
                {detail.maps.map((c) => (
                  <li key={c.index}>
                    <button
                      onClick={() => {
                        setView("chunk");
                        setActiveChunk(c.index);
                      }}
                      className={`block w-full rounded-lg px-3 py-2 text-left text-xs transition ${
                        view === "chunk" && activeChunk === c.index
                          ? "bg-accent-500/10 text-white ring-1 ring-accent-500/40"
                          : "text-ink-200 hover:bg-ink-700/40"
                      } ${c.failed ? "border-l-2 border-red-500/60" : ""}`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-semibold">Chunk {c.index + 1}</span>
                        <span className="text-[10px] text-ink-500">
                          {c.commit_count}c
                        </span>
                      </div>
                      <div className="mt-0.5 text-[10px] text-ink-500">
                        {fmtChunkDateRange(c)}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          <div className="min-h-[400px] px-6 py-5">
            {view === "final" ? (
              <FinalView text={detail.reduce_summary} />
            ) : activeChunk !== null ? (
              <ChunkView
                repoUrl={repoUrl}
                chunk={detail.maps[activeChunk]}
              />
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

function FinalView({ text }: { text: string | null }) {
  if (!text) {
    return <div className="text-sm text-ink-400">No reduce summary written.</div>;
  }
  if (text.trim() === "NO_MAJOR_CHANGES") {
    return (
      <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-6 text-emerald-100">
        <div className="text-lg font-semibold">No major changes</div>
        <div className="mt-1 text-sm text-emerald-200/80">
          The analyzer didn't find anything architecturally significant in this window.
        </div>
      </div>
    );
  }
  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <span className="pill-accent">Final synthesis</span>
      </div>
      <Markdown text={text} />
    </div>
  );
}

function ChunkView({
  repoUrl,
  chunk,
}: {
  repoUrl: string;
  chunk: ChunkMeta;
}) {
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="pill-accent">Chunk {chunk.index + 1}</span>
        <span className="pill">{chunk.commit_count} commits</span>
        <span className="pill">{fmtChunkDateRange(chunk)}</span>
        {chunk.failed && <span className="pill-warn">FAILED</span>}
      </div>
      <details className="mb-4 rounded-lg border border-ink-700/60 bg-ink-900/50">
        <summary className="cursor-pointer px-4 py-2 text-sm text-ink-200">
          Commits in this chunk ({chunk.commits.length})
        </summary>
        <ul className="divide-y divide-ink-700/40 px-4 py-2 text-sm">
          {chunk.commits.map((c) => {
            const url = commitWebUrl(repoUrl, c.hash);
            return (
              <li key={c.hash} className="flex items-start gap-3 py-2">
                {url ? (
                  <a
                    href={url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-xs text-accent-200 hover:text-accent-50"
                  >
                    {c.short}
                  </a>
                ) : (
                  <span className="font-mono text-xs text-ink-400">{c.short}</span>
                )}
                <div className="flex-1">
                  <div className="text-ink-100">{c.subject}</div>
                  <div className="text-[11px] text-ink-500">
                    {c.author} · {formatDate(c.date)}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      </details>
      <Markdown text={chunk.summary || ""} />
    </div>
  );
}

function Markdown({ text }: { text: string }) {
  return (
    <div className="prose-cc">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

function fmtChunkDateRange(c: ChunkMeta): string {
  if (!c.date_first) return "—";
  const a = (c.date_first || "").slice(0, 10);
  const b = (c.date_last || "").slice(0, 10);
  return a === b ? a : `${a} → ${b}`;
}
