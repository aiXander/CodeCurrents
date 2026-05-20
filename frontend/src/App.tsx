import { useCallback, useEffect, useState } from "react";
import { api, ProjectListItem } from "./lib/api";
import { ProjectView } from "./components/ProjectView";
import { Sidebar } from "./components/Sidebar";

export default function App() {
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [activeSlug, setActiveSlug] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      const { projects: ps } = await api.listProjects();
      setProjects(ps);
      setActiveSlug((slug) => {
        if (slug && ps.find((p) => p.slug === slug)) return slug;
        return ps[0]?.slug || null;
      });
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Soft poll the project list so cross-tab active-run pills stay live.
  // Only poll when at least one project has an active run; otherwise the
  // SSE stream on the open project (if any) is the source of truth.
  const anyActive = projects.some((p) => p.active_run_id);
  useEffect(() => {
    if (!anyActive) return;
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh, anyActive]);

  async function handleTrack(url: string) {
    const created = await api.createProject(url);
    await refresh();
    setActiveSlug(created.slug);
  }

  return (
    <div className="flex h-screen w-screen">
      <Sidebar
        projects={projects}
        activeSlug={activeSlug}
        onSelect={setActiveSlug}
        onTrack={handleTrack}
        refreshing={refreshing}
        onRefresh={refresh}
      />
      <main className="flex-1 overflow-y-auto">
        {error && (
          <div className="m-6 rounded-lg border border-red-500/40 bg-red-500/10 p-4 text-red-200">
            <div className="font-semibold">Couldn't reach the backend.</div>
            <div className="mt-1 text-sm text-red-200/80">{error}</div>
            <div className="mt-2 text-xs text-red-200/60">
              Is <span className="font-mono">codecurrents-server</span> running on port 8765?
            </div>
          </div>
        )}
        {!error && activeSlug && (
          <ProjectView
            key={activeSlug}
            slug={activeSlug}
            onDeleted={async () => {
              setActiveSlug(null);
              await refresh();
            }}
            onUpdated={refresh}
          />
        )}
        {!error && !activeSlug && <EmptyState />}
      </main>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center">
      <div className="text-6xl">🌊</div>
      <h1 className="mt-4 text-3xl font-semibold text-white">CodeCurrents</h1>
      <p className="mt-2 max-w-md text-ink-400">
        Paste a GitHub URL in the sidebar to start tracking a repository. Each
        project keeps its own config, state, and a full history of LLM analyses.
      </p>
      <div className="mt-6 grid max-w-2xl grid-cols-1 gap-3 text-sm text-ink-300 md:grid-cols-3">
        <Tip
          title="Local-first"
          body="All state lives under projects/<org_repo>/. Safe to nuke any project folder."
        />
        <Tip
          title="Map-reduce LLM"
          body="Per-chunk summaries cached on disk. Re-runs are cheap; ctrl-C never wastes tokens."
        />
        <Tip
          title="Time-windowed"
          body="Opinionated presets (last 7 days, 30 days, …) or incremental since the last analyzed commit."
        />
      </div>
    </div>
  );
}

function Tip({ title, body }: { title: string; body: string }) {
  return (
    <div className="card p-4 text-left">
      <div className="text-sm font-semibold text-white">{title}</div>
      <div className="mt-1 text-xs text-ink-400">{body}</div>
    </div>
  );
}
