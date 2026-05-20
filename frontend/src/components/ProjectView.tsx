import { useEffect, useRef, useState } from "react";
import { api, ProjectPayload } from "../lib/api";
import { AnalysisRunner } from "./AnalysisRunner";
import { ConfigEditor } from "./ConfigEditor";
import { GithubStatusCard } from "./GithubStatus";
import { RunViewer } from "./RunViewer";

type Props = {
  slug: string;
  onDeleted: () => void;
  onUpdated: () => void;
};

export function ProjectView({ slug, onDeleted, onUpdated }: Props) {
  const [data, setData] = useState<ProjectPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [configDirty, setConfigDirty] = useState(false);
  const flushConfigRef = useRef<(() => Promise<void>) | null>(null);

  async function reload() {
    setErr(null);
    try {
      const d = await api.getProject(slug);
      setData(d);
    } catch (e: any) {
      setErr(e.message || String(e));
    }
  }

  useEffect(() => {
    setLoading(true);
    setData(null);
    reload().finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  if (loading)
    return (
      <div className="flex h-full items-center justify-center text-ink-400">
        Loading project…
      </div>
    );
  if (err)
    return (
      <div className="m-6 rounded-lg border border-red-500/40 bg-red-500/10 p-4 text-red-200">
        {err}
      </div>
    );
  if (!data) return null;

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-6 py-6">
      <Header
        data={data}
        onDelete={async () => {
          if (!confirm(`Untrack ${slug}? This deletes its config, state, and run history.`))
            return;
          await api.deleteProject(slug);
          onDeleted();
        }}
        onRefreshRemote={() => setRefreshKey((k) => k + 1)}
      />

      <GithubStatusCard
        slug={slug}
        repoUrl={data.config.repo_url}
        refreshKey={refreshKey}
      />

      <div className="card p-5">
        <ConfigEditor
          config={data.config}
          hasState={!!data.state.last_commit}
          saving={saving}
          onDirtyChange={setConfigDirty}
          registerFlush={(fn) => {
            flushConfigRef.current = fn;
          }}
          onSave={async (patch) => {
            setSaving(true);
            try {
              const updated = await api.updateConfig(slug, patch);
              setData(updated);
              onUpdated();
            } finally {
              setSaving(false);
            }
          }}
        />
      </div>

      <AnalysisRunner
        slug={slug}
        hasState={!!data.state.last_commit}
        activeRunId={data.active_run?.run_id || null}
        configDirty={configDirty}
        beforeStart={async () => {
          // Auto-save any pending edits so the on-disk config the server
          // reads matches what's visible in the UI.
          if (flushConfigRef.current) {
            await flushConfigRef.current();
          }
        }}
        onStarted={(runId) => {
          // Mark active immediately with the REAL run_id so the sidebar +
          // header pills update and the SSE useEffect doesn't re-attach.
          setData((prev) =>
            prev
              ? { ...prev, active_run: { run_id: runId, status: "running" } }
              : prev
          );
          // Bump the sidebar so its "analyzing…" dot lights up and the
          // app-level polling loop starts (only runs while something is active).
          onUpdated();
        }}
        onDone={async () => {
          await reload();
          setRefreshKey((k) => k + 1);
          onUpdated();
        }}
      />

      <RunViewer
        slug={slug}
        repoUrl={data.config.repo_url}
        runs={data.runs}
        initialRunId={data.runs[0]?.run_id || null}
        onDeleted={reload}
      />
    </div>
  );
}

function Header({
  data,
  onDelete,
  onRefreshRemote,
}: {
  data: ProjectPayload;
  onDelete: () => void;
  onRefreshRemote: () => void;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight text-white">
            {prettyTitle(data.slug)}
          </h1>
          {data.active_run && (
            <span className="pill-warn">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-300" />
              analyzing…
            </span>
          )}
        </div>
        <a
          href={data.github_web_url || data.config.repo_url}
          target="_blank"
          rel="noreferrer"
          className="mt-0.5 inline-block font-mono text-xs text-ink-400 hover:text-accent-200"
        >
          {data.config.repo_url}
        </a>
      </div>
      <div className="flex gap-2">
        <button onClick={onRefreshRemote} className="btn-ghost text-xs">
          Refresh remote
        </button>
        <button onClick={onDelete} className="btn-danger text-xs">
          Untrack
        </button>
      </div>
    </div>
  );
}

function prettyTitle(slug: string): string {
  const idx = slug.indexOf("_");
  if (idx === -1) return slug;
  return `${slug.slice(0, idx)} / ${slug.slice(idx + 1)}`;
}
