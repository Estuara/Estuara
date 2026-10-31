# Deploying the Estuara team preview

> **Status as of 2026-10-31:** the team preview is **live on free hosting**:
> frontend `https://estuara-preview.pages.dev` (Cloudflare Pages) and API
> `https://estuara-api.onrender.com` (Render Free). The zero-cost section at
> the end of this document records the deployment that happened and the test
> results executed against those live URLs.
>
> Python Workers remain blocked by the Cloudflare Free plan: `limits.cpu_ms`
> is rejected there (API error `100328`) and the verified bundle is 9.3 MB
> gzip against the Free plan's 3 MB compressed cap. That single-Worker path is
> the preferred paid architecture and needs no code changes once the account
> is upgraded to Workers Paid.

## Single Worker on Cloudflare (Workers Paid)

The preview is **one Worker, one URL**: `apps/api/wrangler.jsonc` routes
`/api/*` and `/health` to the FastAPI app running on Cloudflare Python
Workers, and serves every other path from Workers Static Assets (the
statically exported Next.js app in `apps/web/out`, which includes the bundled
sample CSV under `/data/`).

```text
                    ┌──────────────── estuara-preview.<subdomain>.workers.dev ────────────────┐
  Browser  ────────►│  Static Assets (apps/web/out)                     /, /data/*.csv, ...   │
                    │  Python Workers (Pyodide + FastAPI, asgi adapter) /api/*, /health       │
                    └─────────────────────────────────────────────────────────────────────────┘
```

### Why this architecture

- **One URL, no CORS.** The API is same-origin with the page, so
  `ESTUARA_ALLOWED_ORIGINS` stays empty (no CORS headers are sent; browsers
  need none). Set it only if you host the frontend separately.
- **No database.** The sample dataset ships with the Worker; analysis is
  stateless and in-memory. Persistent storage (the review registry) is
  deliberately deferred.
- **Python compatibility verified.** The Worker bundles the full dependency
  tree (pandas, NumPy, FastAPI, Pydantic) for the Pyodide runtime:
  1,938 modules, 37.5 MB raw / 9.3 MB gzip, which fits the Workers Paid
  compressed-size limit of 10 MB. A complete 992-row sample analysis was
  executed through `wrangler dev` (the real workerd runtime) and matched the
  local results: 6 vessels, 80 trips, 152 stops, 4 landing-point
  candidates, 2 flagged anomalies.
- **Paid plan required.** `limits.cpu_ms = 60000` in `wrangler.jsonc` needs
  Workers Paid; the Free plan's 10 ms CPU cap cannot finish the sample
  analysis, and the bundle exceeds the Free 3 MB compressed-size limit.

### Requirements

- A Cloudflare account; **Workers Paid** (or trial credits) for the CPU limit
  and bundle size.
- Node.js 20+, pnpm (frontend) and `uv` (Python tooling).
- Authentication: either `npx wrangler login` browser OAuth, or an API token
  in `CLOUDFLARE_API_TOKEN`; `pywrangler` reads the same Wrangler
  credentials.

### Step by step

1. **Build the frontend.**

   ```bash
   cd apps/web
   pnpm install
   pnpm build
   ```

   This copies the canonical sample (`data/samples/lagos_pilot_simulated.csv`,
   the 992-observation file used by tests and analysis) into `public/data/`,
   then exports the app to `apps/web/out`. Never edit `public/data/` directly;
   it is generated and git-ignored — edit the canonical sample instead.

2. **Deploy from the API directory** (the Worker config owns the whole stack):

   ```bash
   cd apps/api
   uv sync --dev
   uv run pywrangler deploy
   ```

   The first output line shows the preview URL, for example
   `https://estuara-preview.<your-subdomain>.workers.dev`.

3. **Verify.**

   ```bash
   curl -s https://estuara-preview.<subdomain>.workers.dev/api/v1/health
   # {"status":"ok",...,"runtime":"cloudflare-workers",...}
   ```

   Then open the URL in a browser: the sample should analyse automatically
   and the map, metrics, trip table and landing-point table should populate.

To remove the preview: `uv run pywrangler delete` (or `npx wrangler delete`).

### Configuration

Everything is set in `apps/api/wrangler.jsonc` as Worker vars — there are no
secrets to create for the preview:

| Binding | Purpose | Preview value |
| --- | --- | --- |
| `ESTUARA_ENV` | environment label returned by health checks | `preview` |
| `ESTUARA_MAX_UPLOAD_MB` | upload refusal before the body is read | `10` (Worker memory is 128 MB) |
| `ESTUARA_MAX_ROWS` | rejection threshold for excessive row counts | `200000` |
| `ESTUARA_ALLOWED_ORIGINS` | CORS list (empty = same-origin, no CORS) | `""` |

If a future secret (for example a registry token) is needed, put it in
Wrangler's secret store rather than the JSON config:

```bash
npx wrangler secret put <NAME>   # prompts for the value; never commit it
```

`wrangler.jsonc` contains no credentials; keep it that way. Local development
credentials live outside the repository (`.dev.vars` is git-ignored).

### Local rehearsal without deploying

```bash
cd apps/web && pnpm build           # refreshes apps/web/out
cd ../api && npx wrangler dev       # real workerd runtime on 127.0.0.1:8787
```

Smoke-test checklist used for both local and deployed rehearsals:

- `GET /` → 200 `text/html`
- `GET /data/lagos_pilot_simulated.csv` → 200, 53,125 bytes
- `GET /api/v1/health` → `"status":"ok"`
- `POST /api/v1/mobility/analyse` with the sample → summary totals match the
  documented baseline (6 vessels / 80 trips / 152 stops / 4 candidates)
- malformed CSV upload → structured `{"detail":{"code":...}}` error, HTTP 422

## Known limitations of the Worker path

- **Static export, not SSR.** The interface is a single client-rendered page
  (`output: "export"`), so Next.js server features are unavailable. The
  Cloudflare Python Workers adapter does not run Node; if the product later
  needs server rendering, revisit `@opennextjs/cloudflare` for the frontend
  and split the API Worker.
- **Cold start.** First request after eviction re-instantiates Pyodide and the
  dependency bundle; expect a few seconds once, then fast responses.
- **No persistence.** Uploads are processed in memory and never stored,
  which is appropriate for the preview but means results cannot be saved.
- **Basemap dependency.** The map loads OpenFreeMap vector tiles from the
  public internet. If tiles are unreachable the map degrades to plain water
  background with route geometry still visible.

## ZERO-COST DEPLOYMENT (Cloudflare Pages Free + Render Free)

A no-spend alternative to the single-Worker architecture above: the frontend
runs on **Cloudflare Pages (Free)**, the existing FastAPI service on a
**Render Free Web Service**, both on their free `*.pages.dev` /
`*.onrender.com` subdomains. The single-Worker `pywrangler` flow is preserved
above as the preferred paid path; nothing in this section removes it.

```text
Browser ──► https://estuara-preview.pages.dev      (Cloudflare Pages, static export)
                │  fetch /api/v1/*  (cross-origin, CORS allow-listed)
                ▼
            https://estuara-api.onrender.com       (Render Free, uvicorn + FastAPI)
```

### Files

- `apps/api/requirements.txt` — runtime dependencies only (mirrors
  `pyproject.toml` ranges; dev tools excluded) for Render's plain-pip build.
- `render.yaml` (repo root) — Render blueprint: Python 3.13, `pip install
  -r requirements.txt && pip install --no-deps .`, `uvicorn
  estuara_api.main:app --host 0.0.0.0 --port $PORT`, health check
  `/api/v1/health`.
- The Workers config (`apps/api/wrangler.jsonc`, `src/worker.py`,
  `pylock.toml`) is untouched.

### Frontend (Cloudflare Pages)

The static export (`apps/web/out`) is deployed as-is — no Pages Functions are
added, so nothing new runs at the edge. The API base URL is compiled in at
build time through `NEXT_PUBLIC_ESTUARA_API_URL` (read by
`apps/web/lib/api.ts`).

### Backend (Render Free)

Same FastAPI app as everywhere else: CORS is already environment-driven
(`ESTUARA_ALLOWED_ORIGINS`, read per request by `ConfiguredCORSMiddleware`),
limits stay environment-driven (`ESTUARA_MAX_UPLOAD_MB`, `ESTUARA_MAX_ROWS`),
the health endpoints (`/health`, `/api/v1/health`) are unchanged, and
uploaded CSVs are processed in memory only — nothing is written to disk or a
database, so there is no persistence to secure.

### How this preview was deployed (2026-10-31)

Two quirks of the free tiers shaped the process:

- **Render's API refuses to create services for accounts without a payment
  method** (HTTP 402 even for the Free plan; the dashboard does not).
  The service was therefore created from the repo's `render.yaml` via
  Render's dashboard blueprint flow (`render.com/deploy?repo=...`).
- **Cloudflare `wrangler pages project create` delegates to the new Workers
  model** and fails without assets; `--force` creates the classic Pages
  project directly. Subsequent `wrangler pages deploy out` calls need no
  `--force`.

1. Push the repo to GitHub (`Estuara/Estuara`); Render builds from it.
2. Create the Pages project and deploy the static export:
   `npx wrangler pages project create estuara-preview --production-branch main --force`,
   then `npx wrangler pages deploy out --project-name estuara-preview --branch main`.
3. Create the Render blueprint, filling `ESTUARA_ALLOWED_ORIGINS` with
   `https://estuara-preview.pages.dev`.
4. Fix the first failed deploy: the build succeeded but the instance could
   not start because `pip` had installed only the dependencies — the
   `estuara_api` package itself lives in `apps/api/src/` and uvicorn could
   not import it. `render.yaml` now runs
   `pip install -r requirements.txt && pip install --no-deps .`.
   Committing that fix auto-deployed and the service went `live`.
5. Rebuild the frontend against the live API and redeploy Pages:

   ```bash
   cd apps/web
   NEXT_PUBLIC_ESTUARA_API_URL=https://estuara-api.onrender.com pnpm build
   npx wrangler pages deploy out --project-name estuara-preview --branch main
   ```

Render deploys automatically on every commit to `main` (blueprint default).

### Live verification results (exactly as executed)

- `GET https://estuara-api.onrender.com/api/v1/health` → 200
  `{"status":"ok","runtime":"python","limits":{"max_upload_bytes":10485760,"max_rows":200000}}`
- `POST .../api/v1/mobility/analyse` with the canonical sample → 200:
  6 vessels, 80 trips, 152 stops, 4 landing candidates, 272.657 km,
  992 input rows, 0 rejected, 2 flagged — identical to the baseline.
- CORS preflight (`OPTIONS`, Origin `https://estuara-preview.pages.dev`)
  → 200 with `access-control-allow-origin: https://estuara-preview.pages.dev`.
- Upload of a synthetic CSV (missing speed column, invalid timestamp,
  invalid latitude, GPS jumps) → 200 with 2 rejected, 3 flagged,
  speeds derived from positions, full warnings/transformations trail.
- Browser-level smoke test (`apps/web/scripts/smoke-tests.mjs`, Playwright
  headless Edge) against the Pages URL: **11/11 pass** — auto-analysis,
  6/80/152/4 metrics, counts row, warnings listed, 4 `Unverified` pills,
  map render, settings-driven method note, demo identity.
- Local checks re-run in the same session: `pytest` 68 passed, `ruff` clean,
  `pnpm lint/typecheck` clean, Next production build clean.

### Free-tier limitations (documented for the team)

- **Render Free sleeps after ~15 minutes idle.** The next request can take
  30-60 s to first response while the instance boots. The interface polls
  `/api/v1/health` before the automatic first analysis and shows a
  "connecting" notice; manual uploads show the same cold-start hint in the
  connectivity banner. A retry succeeds once the service is awake.
- **Spin-down state:** free instances restart on idle (the app is stateless
  by design; nothing is lost).
- **Render Free is 512 MB RAM / shared CPU.** Upload limits (10 MB /
  200,000 rows) are chosen well inside that budget.
- **`*.pages.dev` / `*.onrender.com` subdomains** are shared public
  namespaces: do not rely on them for access control. Anyone with the URL
  can view the sample preview (simulated data only — do not point it at
  real operational datasets without adding authentication).
