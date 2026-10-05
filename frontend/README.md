# Admin frontend

Minimal read-only admin console: login, a sources dashboard, and a pipeline
runs view, with a button to trigger a sync. Plain React + Vite, talks to the
existing FastAPI backend (`src/api/main.py`) over `fetch` — no new backend
routes.

## Run

```
cd frontend
cp .env.example .env   # adjust VITE_API_BASE_URL if the API isn't on :8000
npm install
npm run dev
```

Opens on http://localhost:5173, which is already in the backend's default
`CORS_ORIGINS` (see docker-compose.yml). Requires the backend to be running
(`docker compose up -d`) and at least one admin user (`ADMIN_EMAIL` /
`ADMIN_PASSWORD` env vars bootstrap one on startup).
