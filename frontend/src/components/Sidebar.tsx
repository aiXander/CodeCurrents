import { useState } from "react";
import type { ProjectListItem } from "../lib/api";
import { timeAgo } from "../lib/format";

type Props = {
  projects: ProjectListItem[];
  activeSlug: string | null;
  onSelect: (slug: string) => void;
  onTrack: (url: string) => Promise<void>;
  refreshing: boolean;
  onRefresh: () => void;
};

export function Sidebar({
  projects,
  activeSlug,
  onSelect,
  onTrack,
  refreshing,
  onRefresh,
}: Props) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function handleTrack(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await onTrack(url.trim());
      setUrl("");
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-ink-700/60 bg-ink-900/60">
      <div className="flex items-center justify-between px-4 pt-5 pb-3">
        <div className="flex items-center gap-2">
          <LogoMark />
          <div className="leading-tight">
            <div className="text-sm font-semibold tracking-tight text-white">
              CodeCurrents
            </div>
            <div className="text-[10px] uppercase tracking-widest text-ink-400">
              git pulse · llm digests
            </div>
          </div>
        </div>
        <button
          onClick={onRefresh}
          title="Refresh project list"
          className="rounded-md p-1.5 text-ink-300 hover:bg-ink-700/50 hover:text-white"
        >
          <RefreshIcon spinning={refreshing} />
        </button>
      </div>

      <form onSubmit={handleTrack} className="px-4 pb-4">
        <label className="label">Track new project</label>
        <div className="mt-1.5 flex gap-2">
          <input
            type="text"
            className="input flex-1"
            placeholder="github.com/owner/repo"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={busy}
          />
          <button
            type="submit"
            disabled={busy || !url.trim()}
            className="btn-primary px-3"
          >
            {busy ? "…" : "+"}
          </button>
        </div>
        {err && <div className="mt-2 text-xs text-red-300">{err}</div>}
      </form>

      <div className="flex-1 overflow-y-auto px-2 pb-4">
        <div className="px-2 pb-1 text-[10px] uppercase tracking-widest text-ink-400">
          Tracked Repositories ({projects.length})
        </div>
        {projects.length === 0 && (
          <div className="px-3 py-6 text-center text-xs text-ink-400">
            No projects yet — paste a GitHub URL above to begin.
          </div>
        )}
        <ul className="space-y-1">
          {projects.map((p) => {
            const isActive = p.slug === activeSlug;
            return (
              <li key={p.slug}>
                <button
                  onClick={() => onSelect(p.slug)}
                  className={`group block w-full rounded-lg px-3 py-2 text-left transition ${
                    isActive
                      ? "bg-accent-500/10 ring-1 ring-accent-500/40"
                      : "hover:bg-ink-700/40"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                        p.active_run_id
                          ? "bg-amber-400 animate-pulse"
                          : p.last_run
                          ? "bg-emerald-400"
                          : "bg-ink-500"
                      }`}
                    />
                    <span className="truncate text-sm font-medium text-ink-100">
                      {prettySlug(p.slug)}
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-[11px] text-ink-400">
                    <span className="truncate">
                      {p.branches?.[0] || "main"}
                    </span>
                    <span>·</span>
                    <span className="truncate">
                      {p.last_run ? timeAgo(p.last_run) : "never analyzed"}
                    </span>
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="border-t border-ink-700/60 px-4 py-3 text-[10px] uppercase tracking-widest text-ink-500">
        v0.1 · local
      </div>
    </aside>
  );
}

function prettySlug(slug: string): string {
  // org_repo → "org / repo"
  const idx = slug.indexOf("_");
  if (idx === -1) return slug;
  return `${slug.slice(0, idx)} / ${slug.slice(idx + 1)}`;
}

function LogoMark() {
  return (
    <svg viewBox="0 0 64 64" className="h-9 w-9">
      <defs>
        <linearGradient id="sl" x1="0" x2="1" y1="0" y2="1">
          <stop offset="0" stopColor="#6366f1" />
          <stop offset="1" stopColor="#22d3ee" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill="#161a28" />
      <path
        d="M10 38 C18 22, 26 54, 32 38 S46 22, 54 38"
        stroke="url(#sl)"
        strokeWidth="4"
        strokeLinecap="round"
        fill="none"
      />
      <circle cx="32" cy="22" r="3" fill="#22d3ee" />
    </svg>
  );
}

function RefreshIcon({ spinning }: { spinning: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`h-4 w-4 ${spinning ? "animate-spin" : ""}`}
    >
      <polyline points="23 4 23 10 17 10" />
      <polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15" />
    </svg>
  );
}
