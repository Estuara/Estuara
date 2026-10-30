from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Provenance(BaseModel):
    source_filename: str
    data_status: Literal["real", "user-provided", "public", "estimated", "modelled", "simulated"]
    processed_at: datetime
    input_rows: int
    # input_rows == accepted_rows + rejected_rows. Accepted rows include
    # flagged rows; flagged position jumps are kept here but excluded from
    # movement geometry (see DataQualityReport.excluded_from_geometry_rows).
    accepted_rows: int
    rejected_rows: int
    flagged_rows: int = 0
    transformations: list[str]
    processing_seconds: float | None = None


class ValidationIssue(BaseModel):
    # 1-based data row number in the uploaded CSV (1 = first row after the header).
    row: int | None = None
    vessel_id: str | None = None
    timestamp: datetime | None = None
    field: str | None = None
    code: str
    disposition: Literal["rejected", "flagged"]
    message: str
    # The original value as it appeared in the file, before any transformation.
    value: str | None = None


class QualityThresholds(BaseModel):
    max_plausible_speed_knots: float
    min_position_jump_km: float
    jump_reset_after: int


class DataQualityReport(BaseModel):
    rejected_by_reason: dict[str, int]
    flagged_by_reason: dict[str, int]
    excluded_from_geometry_rows: int
    speeds_derived: int
    speeds_replaced: int
    thresholds: QualityThresholds
    issues: list[ValidationIssue]
    issues_truncated: bool


class AnalysisSettings(BaseModel):
    trip_gap_minutes: int
    stop_speed_knots: float
    stop_min_duration_minutes: float
    landing_radius_meters: float
    landing_min_visits: int
    max_plausible_speed_knots: float


class StopSummary(BaseModel):
    stop_id: str
    trip_id: str
    vessel_id: str
    latitude: float
    longitude: float
    arrived_at: datetime
    departed_at: datetime
    duration_minutes: float
    point_count: int
    classification: Literal["detected_stop"] = "detected_stop"


class TripSummary(BaseModel):
    trip_id: str
    vessel_id: str
    started_at: datetime
    ended_at: datetime
    duration_minutes: float
    distance_km: float
    average_speed_knots: float
    maximum_speed_knots: float
    observation_count: int
    stop_count: int
    origin: list[float]
    destination: list[float]


class NetworkSummary(BaseModel):
    vessels: int
    trips: int
    observations: int
    detected_stops: int
    total_distance_km: float
    total_duration_hours: float
    average_trip_distance_km: float


class LandingPointCandidate(BaseModel):
    candidate_id: str
    latitude: float
    longitude: float
    visit_count: int
    trip_count: int
    vessel_count: int
    mean_dwell_minutes: float
    first_observed_at: datetime
    last_observed_at: datetime
    evidence_score: float
    verification_status: Literal["unverified"] = "unverified"


class LandingPointModelReport(BaseModel):
    model_name: str
    model_version: str
    algorithm: str
    distance_metric: str
    training_point_count: int
    cluster_count: int
    noise_point_count: int
    radius_meters: float
    min_visits: int
    trained_at: datetime


class MobilityAnalysis(BaseModel):
    analysis_id: str
    status: Literal["completed"] = "completed"
    summary: NetworkSummary
    trips: list[TripSummary]
    stops: list[StopSummary]
    landing_points: list[LandingPointCandidate]
    landing_point_report: LandingPointModelReport
    routes_geojson: dict[str, Any]
    stops_geojson: dict[str, Any]
    landing_points_geojson: dict[str, Any]
    provenance: Provenance
    settings: AnalysisSettings
    quality: DataQualityReport
    warnings: list[str] = Field(default_factory=list)

