"""Plausibility screening for GPS observations.

Validation (in `analysis.py`) rejects rows that cannot be used at all: no
parseable timestamp, coordinates outside the globe, no vessel. This module
handles the harder case: rows that are well-formed but physically
implausible. Those are *retained and flagged*, never silently dropped:

- A **position jump** is a fix that would require the vessel to travel faster
  than `max_plausible_speed_knots` from the last trusted fix, over more than
  `min_position_jump_km`. It is excluded from trip geometry, distance and stop
  detection, so a single GPS glitch cannot inflate a trip by tens of
  kilometres, but it stays in the quality report with its original values.
- An **implausible reported speed** is a speed-over-ground value above
  `max_plausible_speed_knots`. The position is kept; the speed used for
  analysis is replaced with the speed derived from neighbouring fixes.

These are screening thresholds, not vessel specifications. A flag means
"inspect this", not "this is proven wrong".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from typing import Literal

import numpy as np
import pandas as pd

EARTH_RADIUS_KM = 6371.0088
KM_PER_NAUTICAL_MILE = 1.852

Disposition = Literal["rejected", "flagged"]


@dataclass(frozen=True)
class QualityThresholds:
    """Explicit, reported plausibility thresholds.

    max_plausible_speed_knots: Lagos commuter ferries and passenger boats
        typically cruise at roughly 8-25 kn; few small craft exceed 35 kn.
        40 kn leaves headroom for fast boats while catching values such as
        the 65 kn spike injected into the simulated sample. The same value
        defines an unreachable position, so it must stay above the fleet's
        real top speed; the API accepts 20-80 kn.
    min_position_jump_km: consumer GNSS error is normally tens of metres.
        Requiring a 250 m displacement keeps ordinary jitter between closely
        spaced fixes (where a 30 m error can imply a high speed) from being
        flagged as a jump.
    jump_reset_after: if this many consecutive fixes all disagree with the
        last trusted fix but agree with each other, the track is treated as
        having genuinely moved (or the earlier fix was the bad one) and the
        new positions are accepted, with a continuity warning.
    """

    max_plausible_speed_knots: float = 40.0
    min_position_jump_km: float = 0.25
    jump_reset_after: int = 3


@dataclass
class IssueRecord:
    row: int | None
    vessel_id: str | None
    timestamp: datetime | None
    field: str | None
    code: str
    disposition: Disposition
    message: str
    value: str | None


@dataclass
class IssueLog:
    """Collects one primary issue per affected row, with complete counts."""

    max_issues: int = 1000
    issues: list[IssueRecord] = field(default_factory=list)
    rejected_by_reason: dict[str, int] = field(default_factory=dict)
    flagged_by_reason: dict[str, int] = field(default_factory=dict)
    truncated: bool = False

    def add(self, record: IssueRecord) -> None:
        counts = (
            self.rejected_by_reason if record.disposition == "rejected" else self.flagged_by_reason
        )
        counts[record.code] = counts.get(record.code, 0) + 1
        if len(self.issues) < self.max_issues:
            self.issues.append(record)
        else:
            self.truncated = True


def haversine_km(
    lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray
) -> np.ndarray:
    """Vectorised great-circle distance in kilometres."""
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = np.radians(lon2) - np.radians(lon1)
    value = np.sin(d_phi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(d_lambda / 2) ** 2
    return EARTH_RADIUS_KM * 2 * np.arctan2(np.sqrt(value), np.sqrt(1 - value))


def epoch_seconds(timestamps: pd.Series) -> np.ndarray:
    return (timestamps - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().to_numpy()


@dataclass(frozen=True)
class JumpResult:
    is_jump: np.ndarray
    continuity_resets: dict[str, int]


def detect_position_jumps(frame: pd.DataFrame, thresholds: QualityThresholds) -> JumpResult:
    """Flags fixes that are physically unreachable from the last trusted fix.

    `frame` must be sorted by vessel and timestamp with unique timestamps per
    vessel. Each fix is compared with the most recent *trusted* fix rather
    than simply the previous row, so the fix after a glitch is not blamed
    for the glitch.
    """
    count = len(frame)
    is_jump = np.zeros(count, dtype=bool)
    resets: dict[str, int] = {}
    if count < 2:
        return JumpResult(is_jump, resets)

    lat = frame["latitude"].to_numpy(dtype=float)
    lon = frame["longitude"].to_numpy(dtype=float)
    seconds = epoch_seconds(frame["timestamp"])
    vessels = frame["vessel_id"].to_numpy()

    def implausible(a: int, b: int) -> bool:
        distance = float(haversine_km(lat[a], lon[a], lat[b], lon[b]))
        hours = (seconds[b] - seconds[a]) / 3600
        knots = distance / KM_PER_NAUTICAL_MILE / hours if hours > 0 else float("inf")
        return (
            distance > thresholds.min_position_jump_km
            and knots > thresholds.max_plausible_speed_knots
        )

    # Cheap vectorised pre-screen: vessels whose consecutive fixes are all
    # plausible cannot contain a jump and skip the sequential pass.
    step_km = haversine_km(lat[:-1], lon[:-1], lat[1:], lon[1:])
    step_hours = np.diff(seconds) / 3600
    with np.errstate(divide="ignore", invalid="ignore"):
        step_knots = step_km / KM_PER_NAUTICAL_MILE / step_hours
    same_vessel = vessels[:-1] == vessels[1:]
    suspicious = (
        same_vessel
        & (step_km > thresholds.min_position_jump_km)
        & (step_knots > thresholds.max_plausible_speed_knots)
    )
    boundaries = np.flatnonzero(~same_vessel) + 1
    starts = np.concatenate([[0], boundaries])
    ends = np.concatenate([boundaries, [count]])

    for start, end in zip(starts, ends):
        if not suspicious[start : max(end - 1, start)].any():
            continue
        trusted = start
        run: list[int] = []
        for index in range(start + 1, end):
            if not implausible(trusted, index):
                is_jump[run] = True
                run, trusted = [], index
                continue
            run.append(index)
            if len(run) >= thresholds.jump_reset_after and not any(
                implausible(a, b) for a, b in pairwise(run)
            ):
                vessel = str(vessels[start])
                resets[vessel] = resets.get(vessel, 0) + 1
                run, trusted = [], index
        is_jump[run] = True
    return JumpResult(is_jump, resets)
