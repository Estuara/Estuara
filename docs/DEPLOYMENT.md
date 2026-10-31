# Deploying the Estuara team preview to Cloudflare

> **Status as of 2026-10-31:** deployment was attempted against the
> `Info@spatialcrestco.com` Cloudflare account, which is on the **Free** plan,
> and is blocked: (1) `limits.cpu_ms` is rejected on the Free plan (API error
> `100328`, "CPU limits are not supported for the Free plan"), and (2) the
> verified bundle is 9.3 MB gzip against the Free plan's 3 MB compressed
> Worker limit. Everything short of the live URL is verified â€” the upload,
> bundle and static assets all succeed, and the full drill ran locally through
> `wrangler dev` (the same workerd runtime) in
> `apps/web/scripts/smoke-tests.mjs`. Upgrading the account to Workers Paid is
> the only remaining step; then re-run Step 2 below.

The preview is **one Worker, one URL**: `apps/api/wrangler.jsonc` routes
`/api/*` and `/health` to the FastAPI app running on Cloudflare Python
Workers, and serves every other path from Workers Static Assets (the
statically exported Next.js app in `apps/web/out`, which includes the bundled
sample CSV under `/data/`).

```text
                    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ estuara-preview.<subdomain>.workers.dev â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
  Browser  â”€â”€â”€â”€â”€â”€â”€â”€â–ºâ”‚  Static Assets (apps/web/out)                     /, /data/*.csv, ...   â”‚
                    â”‚  Python Workers (Pyodide + FastAPI, asgi adapter) /api/*, /health       â”‚
                    â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

## Why this architecture

- **One URL, no CORS.** The API is same-origin with the page, so
  `ESTUARA_ALLOWED_ORIGINS` stays empty (no CORS headers are sent; browsers
  need none). Set it only if you host the frontend separately.
- **No database.** The sample dataset ships with the Worker; analysis is
  stateless and in-memory. Persistent storage (the review registry) is
  deliberately deferred.
- **Python compatibility verified.** The Worker bundles the full dependency
  tree (pandas 3.0.2, NumPy 2.4.6, FastAPI 0.162, Pydantic 2.14) for the
  Pyodide runtime: 1,938 modules, 37.5 MB raw / 9.3 MB gzip, which fits the
  Workers Paid compressed-size limit of 10 MB. A complete 992-row sample
  analysis was executed through `wrangler dev` (the real workerd runtime) and
  matched the local results: 6 vessels, 80 trips, 152 stops, 4 landing-point
  candidates, 2 flagged anomalies.
- **Paid plan required.** `limits.cpu_ms = 60000` in `wrangler.jsonc` needs
  Workers Paid; the Free plan's 10 ms CPU cap cannot finish the sample
  analysis, and the bundle exceeds the Free 3 MB compressed-size limit.

## Requirements

- A Cloudflare account; **Workers Paid** (or trial credits) for the CPU limit
  and bundle size.
- Node.js 20+, pnpm (frontend) and `uv` (Python tooling).
- Authentication: either `npx wrangler login` browser OAuth, or an API token
  in `CLOUDFLARE_API_TOKEN`; `pywrangler` reads the same Wrangler
  credentials from `~/.wrangler/config`.

## Step by step

1. **Build the frontend.**

   ```bash
   cd apps/web
   pnpm install
   pnpm build
   ```

   This copies the canonical sample (`data/samples/lagos_pilot_simulated.csv`,
   the 992-observation file used by tests and analysis) into `public/data/`,
   then exports the app to `apps/web/out`. Never edit `public/data/` directly;
   it is generated and git-ignored â€” edit the canonical sample instead.

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

## Configuration

Everything is set in `apps/api/wrangler.jsonc` as Worker vars â€” there are no
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

## Local rehearsal without deploying

```bash
cd apps/web && pnpm build           # refreshes apps/web/out
cd ../api && npx wrangler dev       # real workerd runtime on 127.0.0.1:8787
```

Smoke-test checklist used for both local and deployed rehearsals:

- `GET /` â†’ 200 `text/html`
- `GET /data/lagos_pilot_simulated.csv` â†’ 200, 53,125 bytes
- `GET /api/v1/health` â†’ `"status":"ok"`
- `POST /api/v1/mobility/analyse` with the sample â†’ summary totals match the
  documented baseline (6 vessels / 80 trips / 152 stops / 4 candidates)
- malformed CSV upload â†’ structured `{"detail":{"code":...}}` error, HTTP 422

## Known deployment limitations

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
Browser â”€â”€â–º https://estuara-preview.pages.dev      (Cloudflare Pages, static export)
                â”‚  fetch /api/v1/*  (cross-origin, CORS allow-listed)
                â–¼
            https://estuara-api.onrender.com       (Render Free, uvicorn + FastAPI)
```

### Files

- `apps/api/requirements.txt` â€” runtime dependencies only (mirrors
  `pyproject.toml` ranges; dev tools excluded) for Render's plain-pip build.
- `render.yaml` (repo root) â€” Render blueprint: Python 3.13, `pip install
  -r requirements.txt`, `uvicorn estuara_api.main:app --host 0.0.0.0 --port
  $PORT`, health check `/api/v1/health`.
- The Workers config (`apps/api/wrangler.jsonc`, `src/worker.py`,
  `pylock.toml`) is untouched.

### Frontend (Cloudflare Pages)

The static export (`apps/web/out`) is deployed as-is â€” no Pages Functions are
added, so nothing new runs at the edge. The API base URL is compiled in at
build time through `NEXT_PUBLIC_ESTUARA_API_URL` (read by
`apps/web/lib/api.ts`).

### Backend (Render Free)

Same FastAPI app as everywhere else: CORS is already environment-driven
(`ESTUARA_ALLOWED_ORIGINS`, read per request by `ConfiguredCORSMiddleware`),
limits stay environment-driven (`ESTUARA_MAX_UPLOAD_MB`, `ESTUARA_MAX_ROWS`),
the health endpoints (`/health`, `/api/v1/health`) are unchanged, and
uploaded CSVs are processed in memory only â€” nothing is written to disk or a
database, so there is no persistence to secure.

### Free-tier limitations (documented for the team)

- **Render Free sleeps after ~15 minutes idle.** The next request can take
  30-60 s to first response while the instance boots. The interface now polls
  `/api/v1/health` before the automatic first analysis and shows a
  "connecting" notice; manual uploads show the same cold-start hint in the
  connectivity banner. Requests may need a retry.
- **Spin-down state:** my free tier restarts wipe the in-memory state (the
  app is stateless by design; nothing to lose).
- **Render Free is 512 MB RAM / shared CPU.** Upload limits (10 MB /
  200,000 rows) are chosen well inside that budget; analysing multiple large
  concurrent uploads is out of scope for a preview.
- **Same Render free instance allows 750 instance-hours/month,** which is
  plenty for one service but means a second free service would share the
  budget.
- **`*.pages.dev` / `*.onrender.com` subdomains** are shared public
  namespaces: do not colocate sensitive branding or rely on them for access
  control. Anyone with the URL can view the sample preview (simulated data
  only â€” do not point it at real operational datasets without adding
  authentication).

### Deployment steps

1. Push this repository to a shared Git remote; Render builds from
   `render.yaml` in the repo (the blueprint references `apps/api`).
2. Render â†’ New â†’ Blueprint â†’ select the repo. On first apply, Render
   prompts for `ESTUARA_ALLOWED_ORIGINS`; fill in the Pages URL from step 4
   (initially `https://estuara-preview.pages.dev`).
3. Wait for the build; verify `GET /api/v1/health` returns
   `{"status":"ok","runtime":"python",...}` on the `*.onrender.com` URL.
4. Build the frontend against that URL and deploy Pages:

   ```bash
   cd apps/web
   NEXT_PUBLIC_ESTUARA_API_URL=https://<service>.onrender.com pnpm build
   npx wrangler pages deploy out --project-name estuara-preview
   ```

5. Verify in a browser: the Pages URL auto-loads the sample, the map /
   metric / tables render, and uploads work cross-origin.

### Verification checklist (live URLs)

Same rehearsal as the Worker smoke test: health endpoint, sample analysis
totals (6/80/152/4), structured error for a malformed CSV, CORS preflight
from the Pages origin, map + basemap render, and the 11-check browser smoke
script pointed at the live Pages URL (`BASE_URL=https://... node
scripts/smoke-tests.mjs`).


