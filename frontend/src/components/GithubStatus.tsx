import { useEffect, useState } from "react";
import { api, GithubStatus as GS } from "../lib/api";
import { formatDate, timeAgo } from "../lib/format";

type Props = {
  slug: string;
  repoUrl: string;
  refreshKey: number;
};

type Clone = {
  exists: boolean;
  path: string;
  blobless?: boolean;
  size_bytes?: number;
};

function fmtBytes(n: number | undefined): string {
  if (!n && n !== 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function GithubStatusCard({ slug, repoUrl, refreshKey }: Props) {
  const [data, setData] = useState<GS | null>(null);
  const [clone, setClone] = useState<Clone | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [cloneBusy, setCloneBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    Promise.all([api.githubStatus(slug), api.cloneStatus(slug)])
      .then(([gh, cl]) => {
        if (cancelled) return;
        setData(gh);
        setClone(cl as Clone);
      })
      .catch((e) => {
        if (!cancelled) setErr(e.message || String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug, refreshKey]);

  async function handleDeleteClone() {
    if (
      !confirm(
        "Delete the cached bare clone? The next Analyze will re-clone from scratch (full clone, faster diff walks)."
      )
    )
      return;
    setCloneBusy(true);
    try {
      await api.deleteClone(slug);
      const fresh = await api.cloneStatus(slug);
      setClone(fresh as Clone);
    } finally {
      setCloneBusy(false);
    }
  }

  if (loading)
    return (
      <div className="card p-4 text-sm text-ink-400">
        Polling GitHub…
      </div>
    );
  if (err)
    return (
      <div className="card p-4 text-sm text-red-300">
        Failed to fetch GitHub status: {err}
      </div>
    );
  if (!data) return null;

  if (!data.available) {
    return (
      <div className="card p-4 text-sm text-ink-300">
        <div className="label mb-1">Remote status</div>
        Not a GitHub repo — skipping remote poll ({data.reason}).
      </div>
    );
  }

  if (data.github_error) {
    return (
      <div className="card p-4 text-sm text-amber-300">
        GitHub error: {data.github_error}
      </div>
    );
  }

  const ahead = data.ahead_by ?? null;
  const showCommits = data.new_commits && data.new_commits.length > 0;

  return (
    <div className="card p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="label">Remote (GitHub)</div>
          <div className="mt-1 flex items-center gap-2 text-sm">
            <a
              href={repoUrl}
              target="_blank"
              rel="noreferrer"
              className="font-medium text-white hover:text-accent-200"
            >
              {data.owner}/{data.repo}
            </a>
            <span className="pill">branch · {data.branch}</span>
            {typeof data.github_stargazers === "number" && (
              <span className="pill">★ {data.github_stargazers}</span>
            )}
          </div>
          {data.github_description && (
            <div className="mt-2 max-w-xl text-sm text-ink-400">
              {data.github_description}
            </div>
          )}
        </div>
        <div className="text-right text-sm">
          <div className="text-ink-400">last push to remote</div>
          <div className="text-ink-100">
            {data.github_pushed_at ? timeAgo(data.github_pushed_at) : "—"}
          </div>
          <div className="text-[11px] text-ink-500">
            {formatDate(data.github_pushed_at)}
          </div>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label="Last analyzed"
          value={data.last_run ? timeAgo(data.last_run) : "never"}
          sub={data.last_run ? formatDate(data.last_run) : "Run an analysis to begin"}
        />
        <Stat
          label="Last commit hash"
          value={data.last_commit ? data.last_commit.slice(0, 10) : "—"}
          sub={data.last_commit ? "stored locally" : "no state yet"}
          mono
        />
        <Stat
          label="New commits on branch"
          value={
            ahead === null
              ? "—"
              : ahead === 0
              ? "0 (up to date)"
              : `${ahead}`
          }
          sub={
            ahead === null
              ? data.last_commit
                ? "couldn't compare"
                : "no baseline — first run will fetch everything"
              : ahead === 0
              ? "branch is at the analyzed commit"
              : "since last analysis"
          }
          accent={ahead !== null && ahead > 0}
        />
        <Stat
          label="Behind (older than analyzed)"
          value={
            data.behind_by === undefined || data.behind_by === null
              ? "—"
              : `${data.behind_by}`
          }
          sub={
            data.behind_by && data.behind_by > 0
              ? "rewritten/dropped commits"
              : "in sync"
          }
        />
      </div>

      {data.compare_error && (
        <div className="mt-3 text-xs text-amber-300">
          compare failed: {data.compare_error}
        </div>
      )}

      {showCommits && (
        <details className="group mt-4 rounded-lg border border-ink-700/60 bg-ink-900/50 p-3 open:bg-ink-900/70">
          <summary className="cursor-pointer text-sm text-ink-200">
            New commits since last analysis ({data.new_commits!.length}
            {ahead && ahead > data.new_commits!.length ? `, showing latest ${data.new_commits!.length}` : ""})
          </summary>
          <ul className="mt-3 space-y-2 text-sm">
            {data.new_commits!.map((c) => (
              <li key={c.sha} className="flex items-start gap-3">
                <a
                  href={c.html_url}
                  target="_blank"
                  rel="noreferrer"
                  className="font-mono text-xs text-accent-200 hover:text-accent-50"
                >
                  {c.short}
                </a>
                <div className="flex-1">
                  <div className="text-ink-100">{c.message}</div>
                  <div className="text-[11px] text-ink-500">
                    {c.author} · {formatDate(c.date)}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}

      {clone && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-ink-700/60 bg-ink-900/40 p-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-ink-300">
            <span className="label">Local clone</span>
            {clone.exists ? (
              <>
                <span className="pill">{fmtBytes(clone.size_bytes)} on disk</span>
                {clone.blobless ? (
                  <span className="pill-warn">
                    blobless · diff walks lazy-fetch over HTTPS (slow)
                  </span>
                ) : (
                  <span className="pill-good">full clone · diff walks are local</span>
                )}
                <span className="font-mono text-[11px] text-ink-500">
                  {clone.path}
                </span>
              </>
            ) : (
              <span className="pill">not cloned yet — first Analyze will clone</span>
            )}
          </div>
          {clone.exists && (
            <button
              onClick={handleDeleteClone}
              disabled={cloneBusy}
              className="btn-ghost text-xs"
              title="Delete the cached bare clone. Next Analyze will re-clone from scratch."
            >
              {cloneBusy ? "Deleting…" : clone.blobless ? "Re-clone (recommended)" : "Delete clone"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  accent,
  mono,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: boolean;
  mono?: boolean;
}) {
  return (
    <div className="rounded-lg border border-ink-700/60 bg-ink-900/40 p-3">
      <div className="label">{label}</div>
      <div
        className={`mt-1 text-base font-semibold ${
          accent ? "text-accent-200" : "text-white"
        } ${mono ? "font-mono text-sm" : ""}`}
      >
        {value}
      </div>
      {sub && <div className="mt-0.5 text-[11px] text-ink-500">{sub}</div>}
    </div>
  );
}
