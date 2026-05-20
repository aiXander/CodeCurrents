import { useEffect, useMemo, useState } from "react";
import type { ProjectConfig } from "../lib/api";
import { TimeWindowPicker } from "./TimeWindowPicker";
import { WINDOW_OPTIONS, WindowKey, windowFromSince } from "../lib/timeWindow";

type Props = {
  config: ProjectConfig;
  hasState: boolean;
  onSave: (patch: Partial<ProjectConfig>) => Promise<void>;
  saving: boolean;
  onDirtyChange?: (dirty: boolean) => void;
  registerFlush?: (flush: (() => Promise<void>) | null) => void;
};

const MODEL_PRESETS = [
  "anthropic/claude-haiku-4-5",
  "anthropic/claude-sonnet-4-5",
  "google/gemini-3.1-flash-lite-preview",
  "google/gemini-3-pro-preview",
  "openai/gpt-5",
  "openai/gpt-5-mini",
];

export function ConfigEditor({
  config,
  hasState,
  onSave,
  saving,
  onDirtyChange,
  registerFlush,
}: Props) {
  const [draft, setDraft] = useState<ProjectConfig>(config);
  const [winKey, setWinKey] = useState<WindowKey>(() => windowFromSince(config.since));
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    setDraft(config);
    setWinKey(windowFromSince(config.since));
  }, [config]);

  const dirty = useMemo(() => {
    return JSON.stringify(draft) !== JSON.stringify(config);
  }, [draft, config]);

  // Tell the parent whether we have unsaved edits, and hand it a "flush"
  // callback it can await right before kicking off Analyze.
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => {
    registerFlush?.(dirty ? () => onSave(draft) : null);
    return () => registerFlush?.(null);
  }, [dirty, draft, onSave, registerFlush]);

  function patch<K extends keyof ProjectConfig>(k: K, v: ProjectConfig[K]) {
    setDraft((d) => ({ ...d, [k]: v }));
  }

  // Time-window pick is the highest-frequency UI gesture and a single click —
  // persist it immediately (along with any other pending draft edits) so
  // "click pill → click Analyze" actually uses the window the user picked.
  async function setWindow(k: WindowKey) {
    setWinKey(k);
    const opt = WINDOW_OPTIONS.find((w) => w.key === k);
    if (!opt) return;
    const next = { ...draft, since: opt.since };
    setDraft(next);
    try {
      await onSave(next);
    } catch {
      /* parent surfaces the error */
    }
  }

  async function handleSave() {
    await onSave(draft);
  }

  function reset() {
    setDraft(config);
    setWinKey(windowFromSince(config.since));
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-base font-semibold text-white">Settings</div>
          <div className="text-xs text-ink-400">
            Saved to <span className="font-mono">projects/{slugify(config.repo_url)}/config.json</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {dirty && (
            <span className="pill-warn">
              <DotIcon /> unsaved
            </span>
          )}
          <button
            type="button"
            className="btn-ghost"
            onClick={reset}
            disabled={!dirty || saving}
          >
            Reset
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={handleSave}
            disabled={!dirty || saving}
          >
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>

      <TimeWindowPicker value={winKey} onChange={setWindow} hasState={hasState} />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Field label="Tracked branch">
          <input
            className="input"
            value={(draft.branches || [])[0] || ""}
            onChange={(e) => patch("branches", [e.target.value])}
            placeholder="main"
          />
          <Hint>The branch the analyzer fetches and walks.</Hint>
        </Field>

        <Field label="LLM model">
          <select
            className="input"
            value={draft.model}
            onChange={(e) => patch("model", e.target.value)}
          >
            {MODEL_PRESETS.includes(draft.model) ? null : (
              <option value={draft.model}>{draft.model} (custom)</option>
            )}
            {MODEL_PRESETS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <Hint>
            OpenRouter (provider/model), direct Anthropic/OpenAI/Gemini, or any custom string.
          </Hint>
        </Field>

        <Field label="Tokens per chunk">
          <input
            type="number"
            className="input"
            value={draft.tokens_per_chunk}
            min={2000}
            max={500000}
            step={1000}
            onChange={(e) =>
              patch("tokens_per_chunk", parseInt(e.target.value || "0", 10))
            }
          />
          <Hint>Larger = fewer LLM calls but heavier prompts.</Hint>
        </Field>

        <Field label="Max commits (safety cap)">
          <input
            type="number"
            className="input"
            value={draft.max_commits}
            min={10}
            max={5000}
            step={10}
            onChange={(e) =>
              patch("max_commits", parseInt(e.target.value || "0", 10))
            }
          />
          <Hint>Fail-fast if the window contains more than this.</Hint>
        </Field>

        <Field label="Concurrent LLM requests">
          <input
            type="number"
            className="input"
            value={draft.max_concurrent_requests}
            min={1}
            max={64}
            step={1}
            onChange={(e) =>
              patch("max_concurrent_requests", parseInt(e.target.value || "0", 10))
            }
          />
          <Hint>Higher = faster but more 429s.</Hint>
        </Field>

        <Field label="Clone directory">
          <input
            className="input font-mono text-xs"
            value={draft.clone_dir}
            onChange={(e) => patch("clone_dir", e.target.value)}
          />
          <Hint>Where bare clones live (blobless).</Hint>
        </Field>
      </div>

      <details
        open={showAdvanced}
        onToggle={(e) => setShowAdvanced((e.target as HTMLDetailsElement).open)}
        className="rounded-xl border border-ink-700/60 bg-ink-900/40 p-4"
      >
        <summary className="cursor-pointer select-none text-sm font-medium text-ink-200">
          Advanced — prompts &amp; ignore patterns
        </summary>

        <div className="mt-4 space-y-4">
          <Field label="Ignore patterns (one per line)">
            <textarea
              className="input font-mono text-xs leading-relaxed"
              rows={8}
              value={(draft.ignore_patterns || []).join("\n")}
              onChange={(e) =>
                patch(
                  "ignore_patterns",
                  e.target.value
                    .split("\n")
                    .map((l) => l.trim())
                    .filter(Boolean)
                )
              }
            />
            <Hint>fnmatch patterns evaluated per file in each diff.</Hint>
          </Field>

          <Field label="Map prompt (per-chunk)">
            <textarea
              className="input font-mono text-xs leading-relaxed"
              rows={10}
              value={draft.map_prompt}
              onChange={(e) => patch("map_prompt", e.target.value)}
            />
          </Field>

          <Field label="Reduce prompt (final synthesis)">
            <textarea
              className="input font-mono text-xs leading-relaxed"
              rows={10}
              value={draft.reduce_prompt}
              onChange={(e) => patch("reduce_prompt", e.target.value)}
            />
          </Field>
        </div>
      </details>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="label mb-1.5 block">{label}</label>
      {children}
    </div>
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return <div className="mt-1 text-[11px] text-ink-500">{children}</div>;
}

function DotIcon() {
  return <span className="h-1.5 w-1.5 rounded-full bg-amber-300" />;
}

function slugify(url: string): string {
  const clean = url.replace(/\.git$/, "").replace(/\/$/, "");
  const m = clean.match(/([^/:]+)\/([^/:]+)$/);
  return m ? `${m[1]}_${m[2]}` : clean;
}
