# CodeCurrents Frontend

A React + Vite + Tailwind UI for [CodeCurrents](../README.md).

## Local development

You need both halves running:

```bash
# Terminal 1 — backend (FastAPI) on :8765
codecurrents-server
# or: python -m codecurrents.server

# Terminal 2 — Vite dev server on :5793
cd frontend
npm install
npm run dev
```

Then open <http://localhost:5793>. The Vite dev server proxies `/api/*` to the
FastAPI backend on `:8765` (override via `CODECURRENTS_API`).

## Production / single-binary local

```bash
cd frontend
npm run build
# dist/ is now served automatically by the backend at http://127.0.0.1:8765
codecurrents-server
```

When `frontend/dist/` exists, the FastAPI server mounts it at `/`, so the whole
GUI is one process.

## Vercel deployment

The frontend is a clean Vite app — point a Vercel project at this directory
(`frontend/`) and it'll build out of the box. To talk to a backend, edit
`vercel.json`:

```json
{
  "rewrites": [
    { "source": "/api/:path*", "destination": "https://your-backend/api/:path*" }
  ]
}
```

Or run the FastAPI server on a small VM / Modal / Railway and replace the
`YOUR-BACKEND-URL` placeholder.

## Storage layout

Everything the UI shows is read from `../projects/<org_repo>/`:

```
projects/
  anthropics_anthropic-sdk-python/
    config.json                  # per-project settings (editable from the UI)
    anthropics_anthropic-sdk-python.json   # state (last commit, last run)
    chunks/                       # disk-backed map-pass cache
    runs/
      20260520-110512/
        meta.json                # commits + chunk metadata for this run
        map_1.md                 # per-chunk LLM summary
        map_2.md
        reduce.md                # final synthesised digest
```

The CLI still works (`codecurrents --repo …`) but does **not** create a
`projects/` entry — only the GUI/`POST /api/projects` does. To tie a CLI run to
a project, pass `--project-slug` (todo) or just use the GUI.
