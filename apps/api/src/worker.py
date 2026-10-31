"""Cloudflare Python Workers entry point.

Wraps the same FastAPI app that uvicorn serves locally. Static assets (the
exported web app and the sample CSV) are served by Workers Static Assets
before this code runs; only /api/* and /health reach Python (see
wrangler.jsonc).
"""

from workers import asgi

from estuara_api.main import app

Default = asgi.entrypoint(app)
