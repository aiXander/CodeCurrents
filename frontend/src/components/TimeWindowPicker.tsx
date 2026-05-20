import { WINDOW_OPTIONS, WindowKey } from "../lib/timeWindow";

type Props = {
  value: WindowKey;
  onChange: (k: WindowKey) => void;
  hasState: boolean;
};

export function TimeWindowPicker({ value, onChange, hasState }: Props) {
  return (
    <div>
      <div className="label mb-2">Time window</div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3 lg:grid-cols-5">
        {WINDOW_OPTIONS.map((w) => {
          const disabled = w.key === "since-last" && !hasState;
          const active = value === w.key;
          return (
            <button
              key={w.key}
              type="button"
              disabled={disabled}
              onClick={() => onChange(w.key)}
              title={disabled ? "Run an analysis first" : w.description}
              className={[
                "rounded-lg border px-3 py-2 text-left transition",
                disabled
                  ? "cursor-not-allowed border-ink-700 bg-ink-800/30 text-ink-500"
                  : active
                  ? "border-accent-500/70 bg-accent-500/10 text-white shadow-glow"
                  : "border-ink-600 bg-ink-800/40 text-ink-200 hover:border-ink-500 hover:bg-ink-700/40",
              ].join(" ")}
            >
              <div className="text-sm font-medium">{w.label}</div>
              <div className="mt-0.5 text-[11px] leading-snug text-ink-400">
                {w.description}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
