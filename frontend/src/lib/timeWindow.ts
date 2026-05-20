export type WindowKey =
  | "since-last"
  | "1d"
  | "3d"
  | "7d"
  | "14d"
  | "30d"
  | "60d"
  | "90d"
  | "180d"
  | "1y";

export type WindowOption = {
  key: WindowKey;
  label: string;
  description: string;
  since: string | null; // null means "use saved state (since last run)"
};

export const WINDOW_OPTIONS: WindowOption[] = [
  {
    key: "since-last",
    label: "Since last run",
    description: "Incremental — only commits added after the previously analyzed commit.",
    since: null,
  },
  {
    key: "1d",
    label: "Last 24 hours",
    description: "Today's activity.",
    since: "1 days ago",
  },
  {
    key: "3d",
    label: "Last 3 days",
    description: "Short-cycle catch-up.",
    since: "3 days ago",
  },
  {
    key: "7d",
    label: "Last 7 days",
    description: "One week digest.",
    since: "7 days ago",
  },
  {
    key: "14d",
    label: "Last 2 weeks",
    description: "Sprint-sized window.",
    since: "14 days ago",
  },
  {
    key: "30d",
    label: "Last 30 days",
    description: "Monthly recap.",
    since: "30 days ago",
  },
  {
    key: "60d",
    label: "Last 2 months",
    description: "Quarter-ish overview.",
    since: "60 days ago",
  },
  {
    key: "90d",
    label: "Last 90 days",
    description: "Full quarter scan.",
    since: "90 days ago",
  },
  {
    key: "180d",
    label: "Last 6 months",
    description: "Half-year arc — heavy LLM cost.",
    since: "180 days ago",
  },
  {
    key: "1y",
    label: "Last year",
    description: "Long view — likely many chunks.",
    since: "365 days ago",
  },
];

export function windowFromSince(since: string | null | undefined): WindowKey {
  if (!since) return "since-last";
  const norm = since.trim().toLowerCase();
  const hit = WINDOW_OPTIONS.find(
    (w) => w.since && w.since.toLowerCase() === norm
  );
  return hit ? hit.key : "7d"; // fall through to weekly default
}
