"""GPS ingestion and marine mobility analysis."""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from itertools import pairwise
from pathlib import Path
from typing import Any

import pandas as pd

from .landing_points import LandingPointClusterer, StopObservation
from .quality import (
    KM_PER_NAUTICAL_MILE,
    IssueLog,
    IssueRecord,
    QualityThresholds,
    detect_position_jumps,
    haversine_km,
)
from .schemas import (
    AnalysisSettings,
    DataQualityReport,
    LandingPointCandidate,
    LandingPointModelReport,
    MobilityAnalysis,
    NetworkSummary,
    Provenance,
    StopSummary,
    TripSummary,
    ValidationIssue,
)
from .schemas import QualityThresholds as QualityThresholdsModel

ALIASES = {
    "timestamp": {"timestamp", "time", "datetime", "recorded_at", "recordedat", "date_time"},
    "latitude": {"latitude", "lat", "y", "gps_latitude"},
    "longitude": {"longitude", "lon", "lng", "long", "x", "gps_longitude"},
    "speed": {"speed", "speed_knots", "sog", "velocity"},
    "vessel_id": {"vessel_id", "vessel", "boat_id", "boat", "device_id"},
}

DEFAULT_MAX_ROWS = 1_000_000

# Offsets such as "Z", "+01:00", "+0100" or "-05" at the end of a timestamp.
_UTC_OFFSET_PATTERN = r"(?:[zZ]|[+-]\d{2}(?::?\d{2})?)\s*$"


class MobilityDataError(ValueError):
    """Raised when uploaded telemetry cannot be analysed safely.

    `code` lets clients distinguish an unreadable file (`malformed_csv`,
    `empty_file`, `too_many_rows`) from a readable file whose content fails
    validation (`missing_columns`, `ambiguous_columns`, `no_valid_rows`).
    """

    def __init__(self, message: str, code: str = "invalid_telemetry") -> None:
        super().__init__(message)
        self.code = code


def _normalise_column(value: str) -> str:
    return "_".join(value.strip().lower().replace("-", " ").split())


def _resolve_columns(columns: list[str]) -> dict[str, str]:
    normalised = {_normalise_column(column): column for column in columns}
    resolved: dict[str, str] = {}
    for canonical, aliases in ALIASES.items():
        matches = [normalised[name] for name in aliases if name in normalised]
        if len(matches) > 1:
            raise MobilityDataError(
                f"Multiple columns could represent '{canonical}': {', '.join(matches)}.",
                code="ambiguous_columns",
            )
        if matches:
            resolved[canonical] = matches[0]

    required = {"timestamp", "latitude", "longitude", "vessel_id"}
    missing = sorted(required - resolved.keys())
    if missing:
        raise MobilityDataError(
            "Missing required telemetry fields: " + ", ".join(missing) + ".",
            code="missing_columns",
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


def _derive_speed_from_positions(frame: pd.DataFrame, trip_gap_minutes: int) -> pd.Series:
    """Speed-over-ground in knots derived from consecutive GPS fixes.

    speed = distance(fix_n-1, fix_n) / elapsed_time(fix_n-1, fix_n)

    This is the same physics a speed sensor reports, computed from position
    instead of trusted from hardware. It only works between two fixes of the
    *same* vessel, in chronological order, so it must run after sorting.

    A fix with no usable predecessor - a vessel's first fix, or the first fix
    after a gap longer than `trip_gap_minutes` (where the average speed over
    the gap says nothing about the vessel's speed at that moment) - borrows
    the next derived value (backfill) rather than being forced to zero, since
    a moving vessel's first observation is not evidence that it was
    stationary. A vessel with a single fix gets 0.
    """
    previous = frame.groupby("vessel_id")[["latitude", "longitude", "timestamp"]].shift()
    distance_km = pd.Series(
        haversine_km(
            previous["latitude"].to_numpy(dtype=float),
            previous["longitude"].to_numpy(dtype=float),
            frame["latitude"].to_numpy(dtype=float),
            frame["longitude"].to_numpy(dtype=float),
        ),
        index=frame.index,
    )
    elapsed_hours = (frame["timestamp"] - previous["timestamp"]).dt.total_seconds() / 3600
    derived_knots = distance_km / elapsed_hours / KM_PER_NAUTICAL_MILE
    derived_knots = derived_knots.where(elapsed_hours.le(trip_gap_minutes / 60))
    return derived_knots.groupby(frame["vessel_id"]).bfill().fillna(0.0)


@dataclass
class PreparedObservations:
    movement: pd.DataFrame
    accepted_rows: int
    rejected_rows: int
    flagged_rows: int
    excluded_from_geometry_rows: int
    speeds_derived: int
    speeds_replaced: int
    transformations: list[str]
    warnings: list[str]
    issues: IssueLog


def _count(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _text(value: Any) -> str | None:
    return None if pd.isna(value) else str(value)


def _timestamp(value: Any) -> datetime | None:
    return None if pd.isna(value) else value.to_pydatetime()


def _parse_timestamps(values: pd.Series) -> pd.Series:
    """ISO 8601 first (fast, unambiguous); anything else via flexible parsing."""
    parsed = pd.to_datetime(values, utc=True, errors="coerce", format="ISO8601")
    retry = parsed.isna() & values.notna()
    if retry.any():
        parsed.loc[retry] = pd.to_datetime(
            values[retry], utc=True, errors="coerce", format="mixed"
        )
    return parsed


def _issue(
    row: pd.Series, code: str, disposition: str, field: str, message: str, value: str | None
) -> IssueRecord:
    return IssueRecord(
        row=int(row["row"]),
        vessel_id=_text(row["vessel_id"]),
        timestamp=_timestamp(row["timestamp"]),
        field=field,
        code=code,
        disposition=disposition,  # type: ignore[arg-type]
        message=message,
        value=value,
    )


def _prepare_frame(
    raw: pd.DataFrame, thresholds: QualityThresholds, trip_gap_minutes: int
) -> PreparedObservations:
    """Validates, de-duplicates and screens observations.

    Returns the movement frame used for trips and stops (rejected rows,
    duplicates and position jumps removed) together with a complete account
    of every row that was rejected, flagged or transformed.
    """
    mapping = _resolve_columns(list(raw.columns))
    source = raw.rename(columns={column: target for target, column in mapping.items()})[
        list(mapping.keys())
    ].reset_index(drop=True)
    issues = IssueLog()
    transformations: list[str] = []
    warnings: list[str] = []
    speed_column_present = "speed" in source

    frame = pd.DataFrame(index=source.index)
    frame["row"] = source.index + 1
    frame["timestamp"] = _parse_timestamps(source["timestamp"])
    frame["latitude"] = pd.to_numeric(source["latitude"], errors="coerce")
    frame["longitude"] = pd.to_numeric(source["longitude"], errors="coerce")
    frame["vessel_id"] = source["vessel_id"].astype("string").str.strip()
    frame["reported_speed"] = (
        pd.to_numeric(source["speed"], errors="coerce") if speed_column_present else float("nan")
    )
    speed_text_present = (
        source["speed"].notna() & source["speed"].astype("string").str.strip().ne("")
        if speed_column_present
        else pd.Series(False, index=source.index)
    )

    # --- Rejections: rows that cannot be placed in space and time. Each row
    # is counted once, under the first reason that applies.
    reasons = [
        ("invalid_timestamp", "timestamp", frame["timestamp"].isna(),
         "Timestamp is missing or could not be parsed."),
        ("invalid_latitude", "latitude", ~frame["latitude"].between(-90, 90),
         "Latitude is missing, non-numeric or outside -90 to 90."),
        ("invalid_longitude", "longitude", ~frame["longitude"].between(-180, 180),
         "Longitude is missing, non-numeric or outside -180 to 180."),
        ("missing_vessel_id", "vessel_id", frame["vessel_id"].isna() | frame["vessel_id"].eq(""),
         "Vessel identifier is missing."),
        ("negative_speed", "speed", frame["reported_speed"].lt(0),
         "Speed over ground cannot be negative."),
    ]  # fmt: skip
    rejected = pd.Series(False, index=frame.index)
    for code, field_name, mask, message in reasons:
        mask = mask.fillna(True).astype(bool) & ~rejected
        for index in frame.index[mask]:
            issues.add(
                _issue(
                    frame.loc[index], code, "rejected", field_name, message,
                    _text(source.at[index, field_name]),
                )
            )  # fmt: skip
        rejected |= mask

    clean = frame.loc[~rejected].sort_values(["vessel_id", "timestamp", "row"], kind="stable")
    if clean.empty:
        raise MobilityDataError(
            "No valid GPS observations remained after validation.", code="no_valid_rows"
        )

    unzoned = int(
        source.loc[clean.index, "timestamp"]
        .astype("string")
        .str.contains(_UTC_OFFSET_PATTERN, regex=True)
        .eq(False)
        .sum()
    )
    if unzoned:
        transformations.append(f"Interpreted {unzoned} timestamps without a UTC offset as UTC.")

    # --- Duplicates: the first observation for each vessel and timestamp is
    # kept. Exact repeats are harmless; conflicting positions are reported
    # separately because they indicate a device or export fault.
    duplicated = clean.duplicated(["vessel_id", "timestamp"], keep="first")
    duplicate_count = int(duplicated.sum())
    if duplicate_count:
        kept_rows = clean.loc[~duplicated].set_index(["vessel_id", "timestamp"])
        compared = ["latitude", "longitude", "reported_speed"]
        exact = 0
        for index in clean.index[duplicated]:
            row = clean.loc[index]
            kept = kept_rows.loc[(row["vessel_id"], row["timestamp"])]
            same = all(
                (pd.isna(row[c]) and pd.isna(kept[c])) or row[c] == kept[c] for c in compared
            )
            exact += int(same)
            message = (
                f"Repeats data row {int(kept['row'])} exactly; the first copy was kept."
                if same
                else f"Same vessel and timestamp as data row {int(kept['row'])} but with "
                "different values; the first observation was kept."
            )
            code = "duplicate_observation" if same else "conflicting_duplicate"
            value = f"{row['latitude']}, {row['longitude']}"
            issues.add(_issue(row, code, "rejected", "timestamp", message, value))
        transformations.append(
            f"Removed {duplicate_count} duplicate vessel/timestamp observations ({exact} exact, "
            f"{duplicate_count - exact} conflicting); the first of each was kept."
        )
        clean = clean.loc[~duplicated]
    clean = clean.reset_index(drop=True)

    # --- Position jumps: retained in the quality report, excluded from movement.
    jumps = detect_position_jumps(clean, thresholds)
    jump_message = (
        "Reaching this position from the previous trusted fix would require more than "
        f"{thresholds.max_plausible_speed_knots:g} kn over more than "
        f"{thresholds.min_position_jump_km:g} km. Excluded from trip geometry, distance and "
        "stop detection."
    )
    for index in clean.index[jumps.is_jump]:
        row = clean.loc[index]
        value = f"{row['latitude']}, {row['longitude']}"
        issues.add(_issue(row, "position_jump", "flagged", "latitude/longitude", jump_message, value))
    jump_count = int(jumps.is_jump.sum())
    if jump_count:
        transformations.append(
            f"Excluded {_count(jump_count, 'position-jump observation', 'position-jump observations')} from trip geometry, distance "
            "and stop detection; they remain listed in the quality report."
        )
        warnings.append(
            f"{_count(jump_count, 'GPS fix implies', 'GPS fixes imply')} movement faster than "
            f"{thresholds.max_plausible_speed_knots:g} kn from the previous trusted position "
            "(likely GPS glitches) and were excluded from trip distances."
        )
    for vessel, resets in jumps.continuity_resets.items():
        warnings.append(
            f"{vessel}: {resets} track discontinuities where {thresholds.jump_reset_after} or "
            "more consecutive fixes disagreed with the earlier position; the newer positions "
            "were accepted. Inspect this vessel's track."
        )
    movement = clean.loc[~jumps.is_jump].reset_index(drop=True)

    # --- Speed: derive where missing or unusable, replace where implausible.
    derived = _derive_speed_from_positions(movement, trip_gap_minutes)
    reported = movement["reported_speed"]
    missing = reported.isna()
    unparseable = missing & speed_text_present.loc[movement["row"] - 1].to_numpy()
    implausible = reported.gt(thresholds.max_plausible_speed_knots)
    movement["speed"] = reported.where(~(missing | implausible), derived)
    movement["speed_source"] = "reported"
    movement.loc[missing, "speed_source"] = "derived"
    movement.loc[implausible, "speed_source"] = "derived_replacement"

    speed_checks = [
        (implausible, "implausible_reported_speed",
         (f"Reported speed exceeds {thresholds.max_plausible_speed_knots:g} kn; position kept, "
          "speed replaced with the position-derived value for analysis.")),
        (unparseable, "invalid_speed_value",
         "Speed value is not numeric; position kept, speed derived from positions."),
    ]  # fmt: skip
    for mask, code, message in speed_checks:
        for index in movement.index[mask]:
            row = movement.loc[index]
            value = _text(source.at[int(row["row"]) - 1, "speed"])
            issues.add(_issue(row, code, "flagged", "speed", message, value))
    derived_implausible = (
        missing & ~unparseable & movement["speed"].gt(thresholds.max_plausible_speed_knots)
    )
    for index in movement.index[derived_implausible]:
        row = movement.loc[index]
        message = (
            f"Speed derived from positions exceeds {thresholds.max_plausible_speed_knots:g} kn; "
            "retained but unreliable."
        )
        value = f"{row['speed']:.2f}"
        issues.add(_issue(row, "implausible_derived_speed", "flagged", "speed", message, value))

    speeds_derived = int(missing.sum())
    speeds_replaced = int(implausible.sum())
    if speeds_derived:
        if not speed_column_present:
            transformations.append(
                f"Speed column was absent; derived speed for all {speeds_derived} "
                "observations from consecutive GPS fixes (distance / elapsed time)."
            )
        else:
            transformations.append(
                f"Derived speed for {speeds_derived} observations with missing or non-numeric "
                "speed values from consecutive GPS fixes (distance / elapsed time)."
            )
        warnings.append(
            f"Speed was derived from positions for {speeds_derived} observations. Derived "
            "speed is sensitive to GPS error and sampling interval, so stop detection is less "
            "reliable than with device-reported speed over ground."
        )
    if speeds_replaced:
        transformations.append(
            f"Replaced {_count(speeds_replaced, 'reported speed', 'reported speeds')} above "
            f"{thresholds.max_plausible_speed_knots:g} kn with position-derived speeds; "
            "original values are kept in the quality report."
        )
        warnings.append(
            f"{_count(speeds_replaced, 'reported speed exceeds', 'reported speeds exceed')} the "
            f"{thresholds.max_plausible_speed_knots:g} kn plausibility threshold and were "
            "replaced with position-derived speeds."
        )

    rejected_rows = int(rejected.sum()) + duplicate_count
    if rejected_rows:
        detail = ", ".join(
            f"{count} {code.replace('_', ' ')}" for code, count in issues.rejected_by_reason.items()
        )
        warnings.insert(0, f"{_count(rejected_rows, 'row was', 'rows were')} rejected ({detail}).")

    return PreparedObservations(
        movement=movement,
        accepted_rows=len(source) - rejected_rows,
        rejected_rows=rejected_rows,
        flagged_rows=sum(issues.flagged_by_reason.values()),
        excluded_from_geometry_rows=jump_count,
        speeds_derived=speeds_derived,
        speeds_replaced=speeds_replaced,
        transformations=transformations,
        warnings=warnings,
        issues=issues,
    )


def _assign_trips(frame: pd.DataFrame, trip_gap_minutes: int) -> pd.DataFrame:
    frame = frame.copy()
    gaps = frame.groupby("vessel_id")["timestamp"].diff().dt.total_seconds().div(60)
    boundaries = gaps.isna() | gaps.gt(trip_gap_minutes)
    frame["trip_number"] = boundaries.groupby(frame["vessel_id"]).cumsum().astype(int)
    frame["trip_id"] = (
        frame["vessel_id"].astype(str) + "-T" + frame["trip_number"].astype(str).str.zfill(3)
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


def _read_csv(content: bytes, max_rows: int) -> pd.DataFrame:
    if not content.strip():
        raise MobilityDataError("The uploaded file is empty.", code="empty_file")
    try:
        # Read every column as text so original values can be quoted back in
        # the quality report exactly as they appeared in the file.
        raw = pd.read_csv(BytesIO(content), dtype=str, nrows=max_rows + 1, encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise MobilityDataError(
            "The file is not UTF-8 encoded text. Export the CSV as UTF-8 and try again.",
            code="malformed_csv",
        ) from exc
    except pd.errors.EmptyDataError as exc:
        raise MobilityDataError("The CSV has no header row or data.", code="empty_file") from exc
    except (pd.errors.ParserError, ValueError) as exc:
        raise MobilityDataError(
            f"The uploaded file is not a readable CSV: {exc}", code="malformed_csv"
        ) from exc
    if raw.empty:
        raise MobilityDataError("The CSV has a header row but no data rows.", code="empty_file")
    if len(raw) > max_rows:
        raise MobilityDataError(
            f"This deployment accepts at most {max_rows:,} observations per upload.",
            code="too_many_rows",
        )
    return raw


def analyse_csv(
    content: bytes,
    filename: str,
    data_status: str = "user-provided",
    trip_gap_minutes: int = 30,
    stop_speed_knots: float = 1.5,
    stop_min_duration_minutes: float = 2.0,
    landing_radius_meters: float = 120.0,
    landing_min_visits: int = 2,
    max_plausible_speed_knots: float = QualityThresholds.max_plausible_speed_knots,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> MobilityAnalysis:
    started_processing = time.perf_counter()
    raw = _read_csv(content, max_rows)
    thresholds = QualityThresholds(max_plausible_speed_knots=max_plausible_speed_knots)
    prepared = _prepare_frame(raw, thresholds, trip_gap_minutes)
    frame = _assign_trips(prepared.movement, trip_gap_minutes)
    stops = _detect_stops(frame, stop_speed_knots, stop_min_duration_minutes)
    stops_per_trip: dict[str, int] = {}
    for stop in stops:
        stops_per_trip[stop.trip_id] = stops_per_trip.get(stop.trip_id, 0) + 1

    trips: list[TripSummary] = []
    route_features: list[dict[str, Any]] = []
    warnings: list[str] = list(prepared.warnings)
    single_fix_trips: list[str] = []
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
            single_fix_trips.append(str(trip_id))
    if single_fix_trips:
        listed = ", ".join(single_fix_trips[:5]) + ("…" if len(single_fix_trips) > 5 else "")
        noun = "segment has" if len(single_fix_trips) == 1 else "segments have"
        warnings.append(
            f"{len(single_fix_trips)} movement {noun} one observation and cannot form route "
            f"geometry: {listed}."
        )

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
    if not stops:
        warnings.append(
            "No stops met the current speed and duration thresholds; this is not proof that no stops occurred."
        )
    if stops and not landing_points:
        warnings.append(
            "No recurring landing-point candidates were found at the current radius and "
            "minimum-visit settings; this does not rule out unobserved landing locations."
        )

    issues = prepared.issues
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
            accepted_rows=prepared.accepted_rows,
            rejected_rows=prepared.rejected_rows,
            flagged_rows=prepared.flagged_rows,
            transformations=prepared.transformations,
            processing_seconds=round(time.perf_counter() - started_processing, 3),
        ),
        settings=AnalysisSettings(
            trip_gap_minutes=trip_gap_minutes,
            stop_speed_knots=stop_speed_knots,
            stop_min_duration_minutes=stop_min_duration_minutes,
            landing_radius_meters=landing_radius_meters,
            landing_min_visits=landing_min_visits,
            max_plausible_speed_knots=max_plausible_speed_knots,
        ),
        quality=DataQualityReport(
            rejected_by_reason=issues.rejected_by_reason,
            flagged_by_reason=issues.flagged_by_reason,
            excluded_from_geometry_rows=prepared.excluded_from_geometry_rows,
            speeds_derived=prepared.speeds_derived,
            speeds_replaced=prepared.speeds_replaced,
            thresholds=QualityThresholdsModel(
                max_plausible_speed_knots=thresholds.max_plausible_speed_knots,
                min_position_jump_km=thresholds.min_position_jump_km,
                jump_reset_after=thresholds.jump_reset_after,
            ),
            issues=[ValidationIssue(**vars(issue)) for issue in issues.issues],
            issues_truncated=issues.truncated,
        ),
        warnings=warnings,
    )
