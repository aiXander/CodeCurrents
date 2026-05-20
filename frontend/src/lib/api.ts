// Thin typed wrapper around the FastAPI backend.

export type ProjectListItem = {
  slug: string;
  repo_url: string;
  branches: string[];
  model: string;
  last_run: string | null;
  last_commit: string | null;
  run_count: number;
  latest_run_id: string | null;
  active_run_id: string | null;
  github_web_url: string | null;
};

export type ProjectConfig = {
  repo_url: string;
  branches: string[];
  since: string | null;
  model: string;
  tokens_per_chunk: number;
  max_commits: number;
  max_concurrent_requests: number;
  clone_dir: string;
  ignore_patterns: string[];
  map_prompt: string;
  reduce_prompt: string;
  start_commit?: string | null;
};

export type ProjectState = {
  last_commit?: string;
  branch?: string;
  last_run?: string;
};

export type RunSummary = {
  run_id: string;
  timestamp: string;
  repo_url: string;
  model: string;
  branches: string[];
  since: string | null;
  commit_count: number;
  chunk_count: number;
  failed_chunks: number;
  first_commit: { hash: string; short: string; date: string } | null;
  last_commit: { hash: string; short: string; date: string } | null;
};

export type ChunkMeta = {
  index: number;
  commit_count: number;
  date_first: string | null;
  date_last: string | null;
  commits: {
    hash: string;
    short: string;
    date: string;
    subject: string;
    author: string;
    author_email: string;
    co_authors: string[];
  }[];
  failed: boolean;
  summary?: string | null;
};

export type RunDetail = {
  run_id: string;
  meta: RunSummary & { chunks: ChunkMeta[] };
  maps: ChunkMeta[];
  reduce_summary: string | null;
};

export type ProjectPayload = {
  slug: string;
  config: ProjectConfig;
  state: ProjectState;
  runs: RunSummary[];
  active_run: { run_id: string; status: string } | null;
  github_web_url: string | null;
};

export type GithubStatus = {
  available: boolean;
  reason?: string;
  branch?: string;
  owner?: string;
  repo?: string;
  last_commit?: string;
  last_run?: string;
  github_pushed_at?: string;
  github_default_branch?: string;
  github_stargazers?: number;
  github_description?: string;
  github_error?: string;
  ahead_by?: number;
  behind_by?: number;
  new_commits?: {
    sha: string;
    short: string;
    message: string;
    author: string;
    date: string;
    html_url: string;
  }[];
  head_commit?: { sha: string; message: string; date: string };
  compare_error?: string;
  head_error?: string;
};

export type Defaults = {
  config: Omit<ProjectConfig, "repo_url">;
  ignore_patterns_default: string[];
  tokens_per_chunk_default: number;
  max_commits_default: number;
  max_concurrent_default: number;
  map_prompt_default: string;
  reduce_prompt_default: string;
};

const BASE = "";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}/api${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    let body: string;
    try {
      body = JSON.stringify(await res.json());
    } catch {
      body = await res.text();
    }
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  defaults: () => req<Defaults>("/defaults"),
  listProjects: () => req<{ projects: ProjectListItem[] }>("/projects"),
  createProject: (repo_url: string) =>
    req<ProjectPayload>("/projects", {
      method: "POST",
      body: JSON.stringify({ repo_url }),
    }),
  getProject: (slug: string) => req<ProjectPayload>(`/projects/${slug}`),
  updateConfig: (slug: string, patch: Partial<ProjectConfig>) =>
    req<ProjectPayload>(`/projects/${slug}/config`, {
      method: "PUT",
      body: JSON.stringify(patch),
    }),
  deleteProject: (slug: string) =>
    req<{ deleted: string }>(`/projects/${slug}`, { method: "DELETE" }),
  githubStatus: (slug: string) =>
    req<GithubStatus>(`/projects/${slug}/github-status`),
  listRuns: (slug: string) =>
    req<{ runs: RunSummary[] }>(`/projects/${slug}/runs`),
  getRun: (slug: string, runId: string) =>
    req<RunDetail>(`/projects/${slug}/runs/${runId}`),
  deleteRun: (slug: string, runId: string) =>
    req<{ deleted: string }>(`/projects/${slug}/runs/${runId}`, {
      method: "DELETE",
    }),
  cloneStatus: (slug: string) =>
    req<{
      exists: boolean;
      path: string;
      blobless?: boolean;
      size_bytes?: number;
      reason?: string;
    }>(`/projects/${slug}/clone-status`),
  deleteClone: (slug: string) =>
    req<{ deleted: boolean; path: string; reason?: string }>(
      `/projects/${slug}/clone`,
      { method: "DELETE" }
    ),
  analyze: (slug: string, reprocess: boolean) =>
    req<{ run_id: string; status: string }>(`/projects/${slug}/analyze`, {
      method: "POST",
      body: JSON.stringify({ reprocess }),
    }),
  runStatus: (slug: string, runId: string) =>
    req<{
      status: string;
      logs: { t: number; line: string }[];
      error: string | null;
      result: string | null;
      started_at: string;
      finished_at: string | null;
    }>(`/projects/${slug}/runs/${runId}/status`),
};

export function streamRun(
  slug: string,
  runId: string,
  onLog: (line: string) => void,
  onDone: (status: string, error: string | null, result: string | null) => void
): () => void {
  const es = new EventSource(`/api/projects/${slug}/runs/${runId}/stream`);
  es.onmessage = (ev) => {
    try {
      const parsed = JSON.parse(ev.data);
      if (parsed.line) onLog(parsed.line);
    } catch {
      /* noop */
    }
  };
  es.addEventListener("done", (ev: MessageEvent) => {
    try {
      const parsed = JSON.parse(ev.data);
      onDone(parsed.status, parsed.error, parsed.result);
    } catch {
      onDone("done", null, null);
    }
    es.close();
  });
  es.onerror = () => {
    es.close();
  };
  return () => es.close();
}
