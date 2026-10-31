# Deploying the Estuara team preview to Cloudflare

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

## Configuration

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

## Local rehearsal without deploying

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
