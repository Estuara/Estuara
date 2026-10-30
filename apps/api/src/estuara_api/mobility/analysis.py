from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from io import BytesIO
from itertools import pairwise
from pathlib import Path
from typing import Any

import pandas as pd

from .landing_points import LandingPointClusterer, StopObservation
from .schemas import (
    LandingPointCandidate,
    LandingPointModelReport,
    MobilityAnalysis,
    NetworkSummary,
    Provenance,
    StopSummary,
    TripSummary,
)

ALIASES = {
    "timestamp": {"timestamp", "time", "datetime", "recorded_at", "recordedat", "date_time"},
    "latitude": {"latitude", "lat", "y", "gps_latitude"},
    "longitude": {"longitude", "lon", "lng", "long", "x", "gps_longitude"},
    "speed": {"speed", "speed_knots", "sog", "velocity"},
    "vessel_id": {"vessel_id", "vessel", "boat_id", "boat", "device_id"},
}


class MobilityDataError(ValueError):
    """Raised when uploaded telemetry cannot be analysed safely."""


def _normalise_column(value: str) -> str:
    return "_".join(value.strip().lower().replace("-", " ").split())


def _resolve_columns(columns: list[str]) -> dict[str, str]:
    normalised = {_normalise_column(column): column for column in columns}
    resolved: dict[str, str] = {}
    for canonical, aliases in ALIASES.items():
        matches = [normalised[name] for name in aliases if name in normalised]
        if len(matches) > 1:
            raise MobilityDataError(
                f"Multiple columns could represent '{canonical}': {', '.join(matches)}."
            )
        if matches:
            resolved[canonical] = matches[0]

    required = {"timestamp", "latitude", "longitude", "vessel_id"}
    missing = sorted(required - resolved.keys())
    if missing:
        raise MobilityDataError(
            "Missing required telemetry fields: " + ", ".join(missing) + "."
        )
    return resolved


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    value = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return radius_km * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _derive_speed_from_positions(frame: pd.DataFrame) -> pd.Series:
    """Fills missing speed-over-ground from consecutive GPS fixes.

    speed = distance(fix_n-1, fix_n) / elapsed_time(fix_n-1, fix_n)

    This is the same physics a speed sensor reports, computed from position
    instead of trusted from hardware. It only works between two fixes of the
    *same* vessel, in chronological order, so it must run after sorting.
    A vessel's very first fix has no predecessor: it borrows the next
    derived value (backfill) rather than being forced to zero, since a
    moving vessel's first observation is not evidence that it was stationary.
    """
    prev_lat = frame.groupby("vessel_id")["latitude"].shift()
    prev_lon = frame.groupby("vessel_id")["longitude"].shift()
    prev_time = frame.groupby("vessel_id")["timestamp"].shift()

    distance_km = pd.Series(
        [
            _haversine_km(a, b, c, d) if pd.notna(a) else float("nan")
            for a, b, c, d in zip(prev_lat, prev_lon, frame["latitude"], frame["longitude"])
        ],
        index=frame.index,
    )
    elapsed_hours = (frame["timestamp"] - prev_time).dt.total_seconds() / 3600
    derived_knots = distance_km / elapsed_hours / 1.852  # 1 nautical mile = 1.852 km
    return derived_knots.groupby(frame["vessel_id"]).bfill().fillna(0.0)


def _prepare_frame(raw: pd.DataFrame) -> tuple[pd.DataFrame, int, list[str]]:
    mapping = _resolve_columns(list(raw.columns))
    selected = raw.rename(columns={source: target for target, source in mapping.items()})[
        list(mapping.keys())
    ].copy()

    selected["timestamp"] = pd.to_datetime(selected["timestamp"], utc=True, errors="coerce")
    for column in ("latitude", "longitude"):
        selected[column] = pd.to_numeric(selected[column], errors="coerce")
    speed_column_present = "speed" in selected
    if not speed_column_present:
        selected["speed"] = float("nan")
    else:
        selected["speed"] = pd.to_numeric(selected["speed"], errors="coerce")
    transformations: list[str] = []

    selected["vessel_id"] = selected["vessel_id"].astype("string").str.strip()
    valid = (
        selected["timestamp"].notna()
        & selected["latitude"].between(-90, 90)
        & selected["longitude"].between(-180, 180)
        & selected["vessel_id"].notna()
        & selected["vessel_id"].ne("")
        & (selected["speed"].isna() | selected["speed"].ge(0))
    )
    rejected = int((~valid).sum())
    clean = selected.loc[valid].sort_values(["vessel_id", "timestamp"]).reset_index(drop=True)
    if clean.empty:
        raise MobilityDataError("No valid GPS observations remained after validation.")

    duplicate_count = int(clean.duplicated(["vessel_id", "timestamp"]).sum())
    if duplicate_count:
        clean = clean.drop_duplicates(["vessel_id", "timestamp"], keep="first")
        transformations.append(
            f"Removed {duplicate_count} duplicate vessel/timestamp observations."
        )
        rejected += duplicate_count

    missing_speed_count = int(clean["speed"].isna().sum())
    if missing_speed_count:
        derived = _derive_speed_from_positions(clean)
        clean["speed"] = clean["speed"].fillna(derived)
        if not speed_column_present:
            transformations.append(
                f"Speed column was absent; derived speed for all {missing_speed_count} "
                "observations from consecutive GPS fixes (distance / elapsed time)."
            )
        else:
            transformations.append(
                f"Derived speed for {missing_speed_count} observations with missing "
                "speed values from consecutive GPS fixes (distance / elapsed time)."
            )
    return clean, rejected, transformations


def _assign_trips(frame: pd.DataFrame, trip_gap_minutes: int) -> pd.DataFrame:
    frame = frame.copy()
    gaps = frame.groupby("vessel_id")["timestamp"].diff().dt.total_seconds().div(60)
    boundaries = gaps.isna() | gaps.gt(trip_gap_minutes)
    frame["trip_number"] = boundaries.groupby(frame["vessel_id"]).cumsum().astype(int)
    frame["trip_id"] = frame.apply(
        lambda row: f"{row['vessel_id']}-T{row['trip_number']:03d}", axis=1
    )
    return frame


def _trip_distance(group: pd.DataFrame) -> float:
    rows = group[["latitude", "longitude"]].to_records(index=False)
    return sum(
        _haversine_km(a.latitude, a.longitude, b.latitude, b.longitude)
        for a, b in pairwise(rows)
    )


def _detect_stops(
    frame: pd.DataFrame, stop_speed_knots: float, stop_min_duration_minutes: float
) -> list[StopSummary]:
    stops: list[StopSummary] = []
    for (trip_id, vessel_id), group in frame.groupby(["trip_id", "vessel_id"], sort=False):
        group = group.copy()
        group["is_slow"] = group["speed"] <= stop_speed_knots
        group["segment"] = group["is_slow"].ne(group["is_slow"].shift()).cumsum()
        stop_index = 0
        for _, segment in group[group["is_slow"]].groupby("segment"):
            arrived = segment["timestamp"].iloc[0]
            departed = segment["timestamp"].iloc[-1]
            duration = (departed - arrived).total_seconds() / 60
            if duration < stop_min_duration_minutes or len(segment) < 2:
                continue
            stop_index += 1
            stops.append(
                StopSummary(
                    stop_id=f"{trip_id}-S{stop_index:02d}",
                    trip_id=str(trip_id),
                    vessel_id=str(vessel_id),
                    latitude=round(float(segment["latitude"].mean()), 7),
                    longitude=round(float(segment["longitude"].mean()), 7),
                    arrived_at=arrived.to_pydatetime(),
                    departed_at=departed.to_pydatetime(),
                    duration_minutes=round(duration, 2),
                    point_count=len(segment),
                )
            )
    return stops


def _feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _discover_landing_points(
    stops: list[StopSummary], radius_meters: float, min_visits: int
) -> tuple[list[LandingPointCandidate], LandingPointModelReport]:
    observations = [
        StopObservation(
            stop_id=stop.stop_id,
            trip_id=stop.trip_id,
            vessel_id=stop.vessel_id,
            latitude=stop.latitude,
            longitude=stop.longitude,
            arrived_at=stop.arrived_at,
            departed_at=stop.departed_at,
            dwell_minutes=stop.duration_minutes,
        )
        for stop in stops
    ]
    model = LandingPointClusterer(radius_meters=radius_meters, min_visits=min_visits).fit(
        observations
    )
    candidates = [
        LandingPointCandidate(
            candidate_id=candidate.candidate_id,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            visit_count=candidate.visit_count,
            trip_count=candidate.trip_count,
            vessel_count=candidate.vessel_count,
            mean_dwell_minutes=candidate.mean_dwell_minutes,
            first_observed_at=candidate.first_observed_at,
            last_observed_at=candidate.last_observed_at,
            evidence_score=candidate.evidence_score,
            verification_status=candidate.verification_status,
        )
        for candidate in model.get_candidates()
    ]
    report = model.get_report()
    return candidates, LandingPointModelReport(
        model_name=report.model_name,
        model_version=report.model_version,
        algorithm=report.algorithm,
        distance_metric=report.distance_metric,
        training_point_count=report.training_point_count,
        cluster_count=report.cluster_count,
        noise_point_count=report.noise_point_count,
        radius_meters=report.radius_meters,
        min_visits=report.min_visits,
        trained_at=report.trained_at,
    )


def analyse_csv(
    content: bytes,
    filename: str,
    data_status: str = "user-provided",
    trip_gap_minutes: int = 30,
    stop_speed_knots: float = 1.5,
    stop_min_duration_minutes: float = 2.0,
    landing_radius_meters: float = 120.0,
    landing_min_visits: int = 2,
) -> MobilityAnalysis:
    try:
        raw = pd.read_csv(BytesIO(content))
    except Exception as exc:
        raise MobilityDataError(f"The uploaded file is not a readable CSV: {exc}") from exc
    if len(raw) > 1_000_000:
        raise MobilityDataError("This release accepts at most 1,000,000 observations per upload.")

    clean, rejected, transformations = _prepare_frame(raw)
    frame = _assign_trips(clean, trip_gap_minutes)
    stops = _detect_stops(frame, stop_speed_knots, stop_min_duration_minutes)
    stops_per_trip: dict[str, int] = {}
    for stop in stops:
        stops_per_trip[stop.trip_id] = stops_per_trip.get(stop.trip_id, 0) + 1

    trips: list[TripSummary] = []
    route_features: list[dict[str, Any]] = []
    warnings: list[str] = []
    for trip_id, group in frame.groupby("trip_id", sort=False):
        distance = _trip_distance(group)
        started, ended = group["timestamp"].iloc[0], group["timestamp"].iloc[-1]
        duration_minutes = max((ended - started).total_seconds() / 60, 0)
        vessel_id = str(group["vessel_id"].iloc[0])
        trip = TripSummary(
            trip_id=str(trip_id),
            vessel_id=vessel_id,
            started_at=started.to_pydatetime(),
            ended_at=ended.to_pydatetime(),
            duration_minutes=round(duration_minutes, 2),
            distance_km=round(distance, 3),
            average_speed_knots=round(float(group["speed"].mean()), 2),
            maximum_speed_knots=round(float(group["speed"].max()), 2),
            observation_count=len(group),
            stop_count=stops_per_trip.get(str(trip_id), 0),
            origin=[float(group["longitude"].iloc[0]), float(group["latitude"].iloc[0])],
            destination=[float(group["longitude"].iloc[-1]), float(group["latitude"].iloc[-1])],
        )
        trips.append(trip)
        if len(group) >= 2:
            route_features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": group[["longitude", "latitude"]].values.tolist(),
                    },
                    "properties": trip.model_dump(mode="json"),
                }
            )
        else:
            warnings.append(f"{trip_id} has one observation and cannot form route geometry.")

    stop_features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [stop.longitude, stop.latitude]},
            "properties": stop.model_dump(mode="json"),
        }
        for stop in stops
    ]
    landing_points, landing_point_report = _discover_landing_points(
        stops, landing_radius_meters, landing_min_visits
    )
    landing_point_features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [candidate.longitude, candidate.latitude]},
            "properties": candidate.model_dump(mode="json"),
        }
        for candidate in landing_points
    ]

    total_distance = sum(trip.distance_km for trip in trips)
    total_minutes = sum(trip.duration_minutes for trip in trips)
    if rejected:
        warnings.append(f"{rejected} observations were rejected or de-duplicated.")
    if not stops:
        warnings.append(
            "No stops met the current speed and duration thresholds; this is not proof that no stops occurred."
        )
    if stops and not landing_points:
        warnings.append(
            "No recurring landing-point candidates were found at the current radius and "
            "minimum-visit settings; this does not rule out unobserved landing locations."
        )

    analysis_id = "mob_" + hashlib.sha256(content).hexdigest()[:12]
    return MobilityAnalysis(
        analysis_id=analysis_id,
        summary=NetworkSummary(
            vessels=int(frame["vessel_id"].nunique()),
            trips=len(trips),
            observations=len(frame),
            detected_stops=len(stops),
            total_distance_km=round(total_distance, 3),
            total_duration_hours=round(total_minutes / 60, 2),
            average_trip_distance_km=round(total_distance / len(trips), 3),
        ),
        trips=trips,
        stops=stops,
        landing_points=landing_points,
        landing_point_report=landing_point_report,
        routes_geojson=_feature_collection(route_features),
        stops_geojson=_feature_collection(stop_features),
        landing_points_geojson=_feature_collection(landing_point_features),
        provenance=Provenance(
            source_filename=Path(filename).name,
            data_status=data_status,  # type: ignore[arg-type]
            processed_at=datetime.now(UTC),
            input_rows=len(raw),
            accepted_rows=len(frame),
            rejected_rows=rejected,
            transformations=transformations,
        ),
        warnings=warnings,
    )
