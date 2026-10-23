# Admin frontend

Full admin console for the Immigration Knowledge backend: Dashboard, Sources
(with per-source and all-sources sync), Review (requirement approvals and
compilation), Rules (dry run, edit, approve, activate — including the typed
"ACTIVATE" confirmation for a not-evaluable field), Documents (semantic/keyword
search, versions, diff), Applicants, and Scheduler. Plain React + Vite +
react-router-dom, talks to the existing FastAPI backend (`src/api/main.py`)
over `fetch` — no UI kit, no CSS framework.

Pipeline-run internals (run IDs, raw pipeline-run tables) are deliberately
not exposed anywhere in the UI — only a non-blocking "sync in progress"
banner and a final plain-language toast summary.

The Docker image is a two-stage build (`frontend/Dockerfile`): Node builds
`dist/`, nginx serves it and proxies `/api` to the API, so production needs no
CORS configuration and no baked-in API host. CI builds the image on every
push but there is no deploy target yet.

When a government host has refused a request (HTTP 403/429), the backend pauses
every source on it for a while. The Sources table surfaces this: a "Paused
until" column and a disabled that-row "Run now" button. "Run all sources" stays
enabled, since the server simply skips paused hosts.

## Run

```
cd frontend
cp .env.example .env   # adjust VITE_API_BASE_URL if the API isn't on :8000
npm install
npm run dev
```

Opens on http://localhost:5173, which is already in the backend's default
`CORS_ORIGINS` (see `docker-compose.yml` — **not** a wildcard; add your own
origin there if you run on a different port). Requires the backend to be
running (`docker compose up -d`) and at least one admin user (`ADMIN_EMAIL` /
`ADMIN_PASSWORD` env vars bootstrap one on startup). Self-registration
(`/auth/register`) is disabled by default in the backend
(`ALLOW_PUBLIC_REGISTRATION=false`) — this console is admin-only and admins
are provisioned out of band, not through a sign-up form.

## Build

```
npm run build      # outputs to dist/ (gitignored, not committed)
npm run preview    # serve the production build locally
```

## Test

```
npm test           # 42 vitest tests, runs once
npm run test:watch # vitest, watch mode
npm run test:e2e   # 5 Playwright flows + axe accessibility checks
```

Uses Vitest + React Testing Library + jsdom; unit tests mock
`src/api/client.js` rather than hitting a real backend. The Playwright suite
runs against a mocked API too, so neither suite needs PostgreSQL or a live
server. CI runs `npm ci`, `npm test`, `npm run build`, and `npm run test:e2e`.

## Structure

```
src/
  api/client.js        — fetch wrapper: base URL from VITE_API_BASE_URL, JWT
                          header, normalized errors, global 401 → logout
  auth/                 — AuthContext (session state), AdminRoute (route guard)
  components/           — Layout, Sidebar, Topbar, DataTable, StatusBadge,
                          Modal, ConfirmDialog, ToastContext, DiffView,
                          DocumentCard, RuleTestPanel, SyncBanner,
                          EmptyState, ErrorState, Skeleton
  hooks/                — useApi (load/error/data), useSyncRunner (Run now /
                          Run all, with polling + a plain-language summary toast)
  pages/                — one file per route
  styles/               — tokens.css (design tokens), base.css (everything else)
```

Auth: the JWT lives in `sessionStorage` (cleared when the tab closes). There
is no refresh endpoint, so **any** 401 from any API call clears the session
and redirects to `/login` with "Session expired". Client-side role checks
(`AdminRoute`) are UX only — every admin route is still enforced server-side.
