import json
import os
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .mobility.analysis import DEFAULT_MAX_ROWS, MobilityDataError, analyse_csv
from .mobility.schemas import MobilityAnalysis

VERSION = "0.1.0"
ANALYSE_PATH = "/api/v1/mobility/analyse"
DEFAULT_MAX_UPLOAD_MB = 50


def _setting(scope: Scope, name: str, default: str) -> str:
    """Reads configuration from Worker bindings, then the process environment.

    On Cloudflare Python Workers, vars and secrets arrive per request in
    `scope["env"]`, not in `os.environ`. Locally (uvicorn) `scope["env"]` is
    absent and `os.environ` is used.
    """
    env = scope.get("env")
    value = getattr(env, name, None) if env is not None else None
    if value is None:
        value = os.environ.get(name)
    return default if value is None or str(value).strip() == "" else str(value)


def _limits(scope: Scope) -> tuple[int, int]:
    max_upload_mb = float(_setting(scope, "ESTUARA_MAX_UPLOAD_MB", str(DEFAULT_MAX_UPLOAD_MB)))
    max_rows = int(_setting(scope, "ESTUARA_MAX_ROWS", str(DEFAULT_MAX_ROWS)))
    return int(max_upload_mb * 1024 * 1024), max_rows


def _error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": {"code": code, "message": message, **extra}})


class ConfiguredCORSMiddleware:
    """CORS whose allowed origins come from ESTUARA_ALLOWED_ORIGINS.

    The value is a comma-separated list of origins. It is resolved on the first
    request because Worker bindings are not available at import time. When it
    is empty (the single-origin Cloudflare deployment), no CORS headers are
    sent: the browser treats the API as same-origin and needs none.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._configured: ASGIApp | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._configured is None and scope["type"] == "http":
            origins = [
                origin.strip()
                for origin in _setting(scope, "ESTUARA_ALLOWED_ORIGINS", "").split(",")
                if origin.strip()
            ]
            self._configured = CORSMiddleware(
                self.app,
                allow_origins=origins,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["*"],
            )
        await (self._configured or self.app)(scope, receive, send)


class UploadSizeLimitMiddleware:
    """Rejects oversized analysis uploads before the body is read.

    A declared Content-Length above the limit is refused immediately, so a
    large upload is never buffered into the Worker's 128 MB memory. Bodies
    without a declared length are checked again after reading.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == ANALYSE_PATH:
            max_bytes, _ = _limits(scope)
            headers = dict(scope.get("headers") or [])
            declared = headers.get(b"content-length")
            if declared is not None and declared.isdigit() and int(declared) > max_bytes:
                response = _error(
                    413,
                    "file_too_large",
                    f"The upload exceeds this deployment's {max_bytes / 1024 / 1024:g} MB limit.",
                    max_bytes=max_bytes,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


app = FastAPI(
    title="Estuara API",
    description="Marine mobility evidence for electrification planning.",
    version=VERSION,
)
app.add_middleware(UploadSizeLimitMiddleware)
app.add_middleware(ConfiguredCORSMiddleware)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return _error(exc.status_code, "http_error", str(exc.detail))


@app.exception_handler(RequestValidationError)
async def parameter_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    problems = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'][1:])}: {error['msg']}"
        for error in exc.errors()
    )
    return _error(
        422,
        "invalid_parameters",
        f"Invalid request: {problems}",
        errors=json.loads(json.dumps(exc.errors(), default=str)),
    )


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    return _error(500, "internal_error", "The analysis service failed unexpectedly.")


@app.get("/health")
@app.get("/api/v1/health")
def health(request: Request) -> dict[str, Any]:
    max_bytes, max_rows = _limits(request.scope)
    return {
        "status": "ok",
        "service": "estuara-api",
        "version": VERSION,
        "runtime": "cloudflare-workers" if request.scope.get("env") is not None else "python",
        "limits": {"max_upload_bytes": max_bytes, "max_rows": max_rows},
    }


@app.post(ANALYSE_PATH, response_model=MobilityAnalysis)
async def analyse_mobility(
    request: Request,
    file: Annotated[UploadFile, File(description="GPS telemetry CSV")],
    data_status: Annotated[
        Literal["real", "user-provided", "public", "estimated", "modelled", "simulated"],
        Form(),
    ] = "user-provided",
    trip_gap_minutes: Annotated[int, Form(ge=5, le=1440)] = 30,
    stop_speed_knots: Annotated[float, Form(ge=0, le=15)] = 1.5,
    stop_min_duration_minutes: Annotated[float, Form(ge=0.5, le=180)] = 2,
    landing_radius_meters: Annotated[float, Form(ge=20, le=2000)] = 120.0,
    landing_min_visits: Annotated[int, Form(ge=2, le=50)] = 2,
    # Below ~20 kn, ordinary ferry movement would be mistaken for GPS jumps.
    max_plausible_speed_knots: Annotated[float, Form(ge=20, le=80)] = 40.0,
) -> MobilityAnalysis:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=415,
            detail={"code": "unsupported_file", "message": "Upload a file with a .csv extension."},
        )
    max_bytes, max_rows = _limits(request.scope)
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "file_too_large",
                "message": f"The CSV exceeds this deployment's {max_bytes / 1024 / 1024:g} MB limit.",
                "max_bytes": max_bytes,
            },
        )
    try:
        return analyse_csv(
            content=content,
            filename=file.filename,
            data_status=data_status,
            trip_gap_minutes=trip_gap_minutes,
            stop_speed_knots=stop_speed_knots,
            stop_min_duration_minutes=stop_min_duration_minutes,
            landing_radius_meters=landing_radius_meters,
            landing_min_visits=landing_min_visits,
            max_plausible_speed_knots=max_plausible_speed_knots,
            max_rows=max_rows,
        )
    except MobilityDataError as exc:
        raise HTTPException(
            status_code=422, detail={"code": exc.code, "message": str(exc)}
        ) from exc
