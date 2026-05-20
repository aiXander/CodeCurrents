import { useEffect, useRef, useState } from "react";
import { api, streamRun } from "../lib/api";

type Props = {
  slug: string;
  hasState: boolean;
  activeRunId: string | null;
  configDirty?: boolean;
  beforeStart?: () => Promise<void>;
  onStarted: (runId: string) => void;
  onDone: () => void;
};

export function AnalysisRunner({
  slug,
  hasState,
  activeRunId,
  configDirty,
  beforeStart,
  onStarted,
  onDone,
}: Props) {
  const [reprocess, setReprocess] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">(
    activeRunId ? "running" : "idle"
  );
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const closerRef = useRef<(() => void) | null>(null);
  const attachedIdRef = useRef<string | null>(null);
  const logBoxRef = useRef<HTMLDivElement>(null);

  // Attach (or re-attach) when an external active run id appears for this
  // slug — e.g. you refreshed the page while a run was in progress. The guard
  // against re-attaching the same id is what stopped the "pending → real id"
  // flicker from blowing away an in-flight SSE.
  useEffect(() => {
    if (!activeRunId || activeRunId === "pending") return;
    if (attachedIdRef.current === activeRunId) return;
    setStatus("running");
    setLogs([]);
    attachStream(activeRunId);
    return () => {
      closerRef.current?.();
      closerRef.current = null;
      attachedIdRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRunId, slug]);

  // Autoscroll logs
  useEffect(() => {
    const el = logBoxRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [logs]);

  function attachStream(runId: string) {
    if (attachedIdRef.current === runId) return;
    closerRef.current?.();
    attachedIdRef.current = runId;
    closerRef.current = streamRun(
      slug,
      runId,
      (line) => setLogs((l) => [...l, line]),
      (st, error) => {
        setStatus(st === "error" ? "error" : "done");
        setErr(error);
        attachedIdRef.current = null;
        onDone();
      }
    );
  }

  async function handleStart() {
    setBusy(true);
    setErr(null);
    setLogs([]);
    setStatus("running");
    try {
      if (beforeStart) {
        await beforeStart();
      }
      const { run_id } = await api.analyze(slug, reprocess);
      onStarted(run_id);
      attachStream(run_id);
    } catch (e: any) {
      setErr(e.message || String(e));
      setStatus("error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700/60 bg-ink-900/50 px-5 py-3">
        <div>
          <div className="text-sm font-semibold text-white">Analyze</div>
          <div className="text-xs text-ink-400">
            Map-reduce LLM summary over the current settings.
          </div>
        </div>

        <label className="flex items-center gap-2 text-xs text-ink-300">
          <input
            type="checkbox"
            checked={reprocess}
            onChange={(e) => setReprocess(e.target.checked)}
            disabled={!hasState}
            className="h-4 w-4 rounded border-ink-500 bg-ink-900 text-accent-500 focus:ring-accent-500/40"
          />
          Reprocess (ignore saved last-commit state)
        </label>
      </div>

      <div className="px-5 py-5">
        {configDirty && status !== "running" && (
          <div className="mb-3 flex items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-300" />
            You have unsaved settings — they'll be saved automatically when you click Analyze.
          </div>
        )}
        <button
          onClick={handleStart}
          disabled={status === "running" || busy}
          className="btn-primary w-full justify-center text-base py-3"
        >
          {status === "running" ? (
            <>
              <SpinnerIcon /> Analyzing — see live log below
            </>
          ) : (
            <>
              <BoltIcon /> Analyze recent changes
            </>
          )}
        </button>
        {err && (
          <div className="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
            {err}
          </div>
        )}

        {(logs.length > 0 || status === "running") && (
          <div className="mt-4">
            <div className="mb-1.5 flex items-center justify-between">
              <div className="label">Live log</div>
              <div className="text-[11px] text-ink-500">{logs.length} lines</div>
            </div>
            <div
              ref={logBoxRef}
              className="log-stream max-h-72 overflow-y-auto rounded-lg border border-ink-700/60 bg-ink-950/80 p-3"
            >
              {logs.map((l, i) => (
                <div key={i} className="log-line text-ink-300">
                  {l}
                </div>
              ))}
              {status === "running" && (
                <div className="log-line text-amber-300 animate-pulse">…</div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function SpinnerIcon() {
  return (
    <svg
      className="h-4 w-4 animate-spin"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
    >
      <circle cx="12" cy="12" r="9" opacity="0.25" />
      <path d="M21 12a9 9 0 0 0-9-9" />
    </svg>
  );
}

function BoltIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      className="h-4 w-4"
      aria-hidden
    >
      <path d="M13 2L4 14h7l-2 8 9-12h-7l2-8z" />
    </svg>
  );
}
