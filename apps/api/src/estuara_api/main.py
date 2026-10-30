from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .mobility.analysis import MobilityDataError, analyse_csv
from .mobility.schemas import MobilityAnalysis

app = FastAPI(
    title="Estuara API",
    description="Marine mobility evidence for electrification planning.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "estuara-api", "version": "0.1.0"}


@app.post("/api/v1/mobility/analyse", response_model=MobilityAnalysis)
async def analyse_mobility(
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
) -> MobilityAnalysis:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=415, detail="Upload a CSV file.")
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The CSV exceeds the 50 MB upload limit.")
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
        )
    except MobilityDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

