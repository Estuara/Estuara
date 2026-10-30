"""Unsupervised discovery of recurring landing-point candidates from detected stops.

A detected stop (see `analysis.py`) is one vessel's low-speed dwell during one
trip. It is not yet evidence of a place. `LandingPointClusterer` fits a DBSCAN
model over many stops, across many trips and vessels, to find coordinates
where stops keep recurring closely together. Those recurring locations become
"landing-point candidates": possible terminals or informal landing points that
still require human verification.

This is unsupervised clustering, not supervised training. There are no
labelled landing points to learn from yet, so the model cannot report
precision, recall or accuracy. It can only describe the structure it found in
the stop observations it was given.

DBSCAN is implemented here in NumPy rather than imported from scikit-learn.
scikit-learn pulls in SciPy, which together add ~58 MB to a Cloudflare Python
Worker bundle (over the 64 MiB limit), cannot be imported during Worker
startup, and took ~13 s to import lazily on a cold start. The port follows
scikit-learn's algorithm (core points have at least `min_samples` neighbours
within `eps`, counting themselves; clusters expand depth-first from core
points in index order), and `tests/test_landing_points.py` checks that its
labels match scikit-learn's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import numpy as np

EARTH_RADIUS_METERS = 6_371_008.8
# Upper bound on pairwise-distance matrix cells held in memory at once
# (~4 MB of float64 per temporary array), so large stop sets stay well inside
# the 128 MB Worker memory limit.
_MAX_MATRIX_CELLS = 500_000

MODEL_NAME = "landing_point_clusterer"
MODEL_VERSION = "0.1.0"
ALGORITHM = "DBSCAN"
DISTANCE_METRIC = "haversine"

VerificationStatus = Literal["unverified"]


class LandingPointConfigError(ValueError):
    """Raised when the clusterer is configured with unusable parameters."""


class ModelNotFittedError(RuntimeError):
    """Raised when candidates or a report are requested before `fit()` runs."""


@dataclass(frozen=True)
class StopObservation:
    """One detected, low-speed vessel dwell. The clustering model's raw input."""

    stop_id: str
    trip_id: str
    vessel_id: str
    latitude: float
    longitude: float
    arrived_at: datetime
    departed_at: datetime
    dwell_minutes: float


@dataclass(frozen=True)
class LandingPointCandidate:
    """A recurring location discovered by the model. Not a verified terminal."""

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
    verification_status: VerificationStatus = "unverified"


@dataclass(frozen=True)
class ClusteringReport:
    """Metadata describing how the model was fitted, not how well it performs."""

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


def _evidence_score(visit_count: int, vessel_count: int, min_visits: int) -> float:
    """A bounded, explainable prioritisation score in [0, 1]. Not a probability.

    It rewards two independent signals a real landing point should show:
    repeated visits well beyond the minimum, and use by more than one vessel
    (a single vessel idling repeatedly at the same spot is weaker evidence
    than several different vessels converging on it). Each signal saturates
    so no single very active vessel can dominate the score.
    """
    visit_component = min(visit_count / (min_visits * 4), 1.0)
    vessel_component = min(vessel_count / 3, 1.0)
    return round(0.6 * visit_component + 0.4 * vessel_component, 3)


def _haversine_neighbourhoods(coordinates_rad: np.ndarray, eps_radians: float) -> list[np.ndarray]:
    """Indices of every point within `eps_radians` great-circle distance of each point.

    Uses the same haversine formula as scikit-learn's `metric="haversine"`,
    processed in row chunks so memory stays bounded.
    """
    count = len(coordinates_rad)
    lat, lon = coordinates_rad[:, 0], coordinates_rad[:, 1]
    cos_lat = np.cos(lat)
    chunk = max(1, _MAX_MATRIX_CELLS // max(count, 1))
    neighbourhoods: list[np.ndarray] = []
    for start in range(0, count, chunk):
        stop = min(start + chunk, count)
        d_lat = lat[start:stop, None] - lat[None, :]
        d_lon = lon[start:stop, None] - lon[None, :]
        value = (
            np.sin(d_lat / 2) ** 2
            + cos_lat[start:stop, None] * cos_lat[None, :] * np.sin(d_lon / 2) ** 2
        )
        distance = 2 * np.arcsin(np.sqrt(np.clip(value, 0.0, 1.0)))
        neighbourhoods.extend(np.flatnonzero(row) for row in distance <= eps_radians)
    return neighbourhoods


def dbscan_haversine(coordinates_rad: np.ndarray, eps_radians: float, min_samples: int) -> np.ndarray:
    """DBSCAN cluster labels (-1 for noise) for [lat, lon] points in radians."""
    neighbourhoods = _haversine_neighbourhoods(coordinates_rad, eps_radians)
    is_core = np.array([len(neighbours) >= min_samples for neighbours in neighbourhoods])
    labels = np.full(len(coordinates_rad), -1, dtype=int)
    label = 0
    for seed in range(len(coordinates_rad)):
        if labels[seed] != -1 or not is_core[seed]:
            continue
        stack = [seed]
        while stack:
            point = stack.pop()
            if labels[point] != -1:
                continue
            labels[point] = label
            if is_core[point]:
                stack.extend(int(n) for n in neighbourhoods[point] if labels[n] == -1)
        label += 1
    return labels


class LandingPointClusterer:
    """Discovers recurring landing-point candidates from detected vessel stops.

    Fits a DBSCAN model using the haversine metric so the configured radius
    behaves as a real geographic distance rather than a distance in raw
    latitude/longitude degrees (which are not uniform in size on the Earth's
    surface). Isolated stops that never recur near enough to others remain
    noise and are not reported as candidates.
    """

    def __init__(self, radius_meters: float, min_visits: int) -> None:
        if radius_meters <= 0:
            raise LandingPointConfigError("radius_meters must be greater than zero.")
        if min_visits < 2:
            raise LandingPointConfigError(
                "min_visits must be at least 2; a single stop cannot be a recurring pattern."
            )
        self.radius_meters = float(radius_meters)
        self.min_visits = int(min_visits)
        self._fitted = False
        self._candidates: list[LandingPointCandidate] = []
        self._report: ClusteringReport | None = None

    def fit(self, stops: list[StopObservation]) -> LandingPointClusterer:
        """Fits the clustering model over the supplied stop observations."""
        labels = self._cluster_labels(stops)
        self._candidates = self._build_candidates(stops, labels)
        noise_count = int(np.sum(labels == -1)) if len(stops) else 0
        self._report = ClusteringReport(
            model_name=MODEL_NAME,
            model_version=MODEL_VERSION,
            algorithm=ALGORITHM,
            distance_metric=DISTANCE_METRIC,
            training_point_count=len(stops),
            cluster_count=len(self._candidates),
            noise_point_count=noise_count,
            radius_meters=self.radius_meters,
            min_visits=self.min_visits,
            trained_at=datetime.now(UTC),
        )
        self._fitted = True
        return self

    def get_candidates(self) -> list[LandingPointCandidate]:
        """Returns discovered candidates. Requires `fit()` to have run."""
        self._require_fitted()
        return list(self._candidates)

    def get_report(self) -> ClusteringReport:
        """Returns the training report. Requires `fit()` to have run."""
        self._require_fitted()
        assert self._report is not None  # guaranteed once _fitted is True
        return self._report

    def _require_fitted(self) -> None:
        if not self._fitted:
            raise ModelNotFittedError(
                "LandingPointClusterer has no results yet; call fit() first."
            )

    def _cluster_labels(self, stops: list[StopObservation]) -> np.ndarray:
        if not stops:
            return np.array([], dtype=int)
        coordinates_deg = np.array([[stop.latitude, stop.longitude] for stop in stops])
        coordinates_rad = np.radians(coordinates_deg)
        eps_radians = self.radius_meters / EARTH_RADIUS_METERS
        return dbscan_haversine(coordinates_rad, eps_radians, self.min_visits)

    def _build_candidates(
        self, stops: list[StopObservation], labels: np.ndarray
    ) -> list[LandingPointCandidate]:
        candidates: list[LandingPointCandidate] = []
        for rank, cluster_id in enumerate(sorted(set(labels) - {-1}), start=1):
            members = [stop for stop, label in zip(stops, labels) if label == cluster_id]
            trips = {member.trip_id for member in members}
            vessels = {member.vessel_id for member in members}
            candidates.append(
                LandingPointCandidate(
                    candidate_id=f"LP-{rank:03d}",
                    latitude=round(float(np.mean([m.latitude for m in members])), 7),
                    longitude=round(float(np.mean([m.longitude for m in members])), 7),
                    visit_count=len(members),
                    trip_count=len(trips),
                    vessel_count=len(vessels),
                    mean_dwell_minutes=round(
                        float(np.mean([m.dwell_minutes for m in members])), 2
                    ),
                    first_observed_at=min(m.arrived_at for m in members),
                    last_observed_at=max(m.departed_at for m in members),
                    evidence_score=_evidence_score(len(members), len(vessels), self.min_visits),
                    verification_status="unverified",
                )
            )
        return candidates
