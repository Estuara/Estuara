"""Data-quality behaviour: rejection, flagging, speed derivation and provenance."""

import pytest

from estuara_api.mobility.analysis import MobilityDataError, analyse_csv

from .test_analysis import SAMPLE_CSV_PATH

HEADER = b"timestamp,latitude,longitude,speed,vessel_id\n"

# One vessel moving east at a steady pace: 0.01 deg of longitude near 6.5 N is
# ~1.105 km, covered every 2 minutes, i.e. ~17.9 kn.
STEADY = (
    b"2026-09-15T08:00:00Z,6.5000,3.9000,18.0,V1\n"
    b"2026-09-15T08:02:00Z,6.5000,3.9100,18.0,V1\n"
    b"2026-09-15T08:04:00Z,6.5000,3.9200,18.0,V1\n"
    b"2026-09-15T08:06:00Z,6.5000,3.9300,18.0,V1\n"
    b"2026-09-15T08:08:00Z,6.5000,3.9400,18.0,V1\n"
)


def _assert_rows_accounted_for(result) -> None:
    provenance = result.provenance
    quality = result.quality
    assert provenance.input_rows == provenance.accepted_rows + provenance.rejected_rows
    assert provenance.rejected_rows == sum(quality.rejected_by_reason.values())
    assert provenance.flagged_rows == sum(quality.flagged_by_reason.values())
    assert result.summary.observations == (
        provenance.accepted_rows - quality.excluded_from_geometry_rows
    )


# --- Speed derivation -------------------------------------------------------


def test_speed_is_derived_when_speed_column_is_missing() -> None:
    content = b"timestamp,latitude,longitude,vessel_id\n" + b"".join(
        b",".join(line.split(b",")[:3] + line.split(b",")[4:]) + b"\n"
        for line in STEADY.strip().split(b"\n")
    )
    result = analyse_csv(content, "no_speed.csv")
    trip = result.trips[0]
    assert trip.average_speed_knots == pytest.approx(17.9, abs=0.2)
    assert result.quality.speeds_derived == 5
    assert any("Speed column was absent" in t for t in result.provenance.transformations)
    assert any("derived from positions" in w for w in result.warnings)
    _assert_rows_accounted_for(result)


def test_first_fix_borrows_next_derived_speed_rather_than_zero() -> None:
    content = HEADER + STEADY.replace(b",18.0,", b",,")
    result = analyse_csv(content, "blank_speed.csv")
    # If the first fix were forced to zero, the trip would start with a stop.
    assert result.summary.detected_stops == 0
    assert result.trips[0].maximum_speed_knots == pytest.approx(17.9, abs=0.2)


def test_speed_is_not_derived_across_a_trip_gap() -> None:
    # Two fixes 2 h apart and 11 km apart. Averaging over the gap would give
    # ~3 kn; the second trip must instead use its own next fix (~18 kn).
    content = HEADER + (
        b"2026-09-15T08:00:00Z,6.5000,3.9000,,V1\n"
        b"2026-09-15T08:02:00Z,6.5000,3.9100,,V1\n"
        b"2026-09-15T10:02:00Z,6.5000,4.0100,,V1\n"
        b"2026-09-15T10:04:00Z,6.5000,4.0200,,V1\n"
    )
    result = analyse_csv(content, "gap.csv", trip_gap_minutes=30)
    assert result.summary.trips == 2
    assert all(trip.average_speed_knots > 15 for trip in result.trips)


def test_non_numeric_speed_is_flagged_and_derived_not_rejected() -> None:
    content = HEADER + STEADY.replace(
        b"08:04:00Z,6.5000,3.9200,18.0", b"08:04:00Z,6.5000,3.9200,fast"
    )
    result = analyse_csv(content, "text_speed.csv")
    assert result.provenance.rejected_rows == 0
    assert result.quality.flagged_by_reason == {"invalid_speed_value": 1}
    issue = result.quality.issues[0]
    assert (issue.row, issue.value, issue.disposition) == (3, "fast", "flagged")
    _assert_rows_accounted_for(result)


# --- Timestamps ---------------------------------------------------------------


def test_missing_and_invalid_timestamps_are_rejected_with_reasons() -> None:
    content = HEADER + STEADY + b",6.5,3.95,18,V1\nnot-a-date,6.5,3.96,18,V1\n"
    result = analyse_csv(content, "bad_time.csv")
    assert result.provenance.rejected_rows == 2
    assert result.quality.rejected_by_reason == {"invalid_timestamp": 2}
    rows = {issue.row: issue.value for issue in result.quality.issues}
    assert rows == {6: None, 7: "not-a-date"}
    assert "2 rows were rejected (2 invalid timestamp)" in result.warnings[0]
    _assert_rows_accounted_for(result)


def test_non_iso_timestamps_are_parsed_and_unzoned_ones_reported() -> None:
    content = HEADER + b"2026-09-15 08:00:00,6.5,3.90,18,V1\n15 Sep 2026 08:02,6.5,3.91,18,V1\n"
    result = analyse_csv(content, "formats.csv")
    assert result.provenance.rejected_rows == 0
    assert any("without a UTC offset as UTC" in t for t in result.provenance.transformations)


def test_all_rows_invalid_raises_a_coded_error() -> None:
    with pytest.raises(MobilityDataError) as error:
        analyse_csv(HEADER + b"never,6.5,3.9,1,V1\n", "none.csv")
    assert error.value.code == "no_valid_rows"


# --- Coordinates ----------------------------------------------------------------


@pytest.mark.parametrize(
    "line,code",
    [
        (b"2026-09-15T09:00:00Z,91,3.9,1,V1\n", "invalid_latitude"),
        (b"2026-09-15T09:00:00Z,abc,3.9,1,V1\n", "invalid_latitude"),
        (b"2026-09-15T09:00:00Z,6.5,-181,1,V1\n", "invalid_longitude"),
        (b"2026-09-15T09:00:00Z,6.5,,1,V1\n", "invalid_longitude"),
        (b"2026-09-15T09:00:00Z,6.5,3.9,1,\n", "missing_vessel_id"),
        (b"2026-09-15T09:00:00Z,6.5,3.9,-2,V1\n", "negative_speed"),
    ],
)
def test_invalid_rows_are_rejected_with_specific_reason(line: bytes, code: str) -> None:
    result = analyse_csv(HEADER + STEADY + line, "bad.csv")
    assert result.quality.rejected_by_reason == {code: 1}
    assert result.quality.issues[0].row == 6
    assert result.quality.issues[0].disposition == "rejected"
    _assert_rows_accounted_for(result)


def test_row_with_several_problems_is_counted_once() -> None:
    result = analyse_csv(HEADER + STEADY + b"nope,999,999,-1,\n", "multi.csv")
    assert result.provenance.rejected_rows == 1
    assert result.quality.rejected_by_reason == {"invalid_timestamp": 1}


# --- Duplicates ---------------------------------------------------------------


def test_exact_and_conflicting_duplicates_are_distinguished() -> None:
    content = HEADER + STEADY + (
        b"2026-09-15T08:02:00Z,6.5000,3.9100,18.0,V1\n"  # exact copy of row 2
        b"2026-09-15T08:04:00Z,6.6000,3.9200,18.0,V1\n"  # same time as row 3, moved
    )
    result = analyse_csv(content, "dupes.csv")
    assert result.quality.rejected_by_reason == {
        "duplicate_observation": 1,
        "conflicting_duplicate": 1,
    }
    messages = {issue.row: issue.message for issue in result.quality.issues}
    assert "data row 2" in messages[6]
    assert "data row 3" in messages[7]
    # The first observation is kept, so the moved copy does not distort distance.
    assert result.trips[0].distance_km == pytest.approx(4.42, abs=0.02)
    assert any("1 exact, 1 conflicting" in t for t in result.provenance.transformations)
    _assert_rows_accounted_for(result)


def test_same_timestamp_for_different_vessels_is_not_a_duplicate() -> None:
    content = HEADER + STEADY + STEADY.replace(b",V1\n", b",V2\n")
    result = analyse_csv(content, "two.csv")
    assert result.provenance.rejected_rows == 0
    assert result.summary.vessels == 2


# --- Suspicious speeds ------------------------------------------------------------


def test_implausible_reported_speed_is_flagged_kept_and_replaced() -> None:
    content = HEADER + STEADY.replace(b"08:04:00Z,6.5000,3.9200,18.0", b"08:04:00Z,6.5000,3.9200,65.0")
    result = analyse_csv(content, "spike.csv")
    assert result.provenance.rejected_rows == 0
    assert result.quality.flagged_by_reason == {"implausible_reported_speed": 1}
    assert result.quality.speeds_replaced == 1
    issue = result.quality.issues[0]
    assert (issue.row, issue.value, issue.field) == (3, "65.0", "speed")
    # Summaries use the derived replacement, not the spike.
    assert result.trips[0].maximum_speed_knots < 20
    assert result.summary.observations == 5
    _assert_rows_accounted_for(result)


def test_speed_threshold_is_configurable_and_reported() -> None:
    content = HEADER + STEADY.replace(b"08:04:00Z,6.5000,3.9200,18.0", b"08:04:00Z,6.5000,3.9200,30.0")
    default = analyse_csv(content, "default.csv")
    assert default.quality.flagged_by_reason == {}
    strict = analyse_csv(content, "strict.csv", max_plausible_speed_knots=25)
    assert strict.quality.flagged_by_reason == {"implausible_reported_speed": 1}
    assert strict.settings.max_plausible_speed_knots == 25
    assert strict.quality.thresholds.max_plausible_speed_knots == 25
    # The positions are consistent (~18 kn), so this is a speed problem, not a jump.
    assert strict.quality.excluded_from_geometry_rows == 0


def test_threshold_below_cruising_speed_treats_real_movement_as_jumps() -> None:
    # Documents why the API restricts the setting to >= 20 kn: the same
    # threshold defines "unreachable", so setting it below the vessel's real
    # speed makes genuine movement look like GPS glitches.
    result = analyse_csv(HEADER + STEADY, "too_strict.csv", max_plausible_speed_knots=15)
    assert result.quality.flagged_by_reason.get("position_jump", 0) > 0


# --- GPS jumps ----------------------------------------------------------------------


def test_single_gps_jump_is_flagged_and_excluded_from_distance() -> None:
    jump = b"2026-09-15T08:05:00Z,6.8000,3.9250,18.0,V1\n"  # ~33 km north for 1 minute
    lines = STEADY.split(b"\n")
    content = HEADER + b"\n".join(lines[:3]) + b"\n" + jump + b"\n".join(lines[3:])
    result = analyse_csv(content, "jump.csv")
    assert result.quality.flagged_by_reason == {"position_jump": 1}
    assert result.quality.excluded_from_geometry_rows == 1
    assert result.quality.issues[0].row == 4
    assert result.trips[0].distance_km == pytest.approx(4.42, abs=0.02)
    coordinates = result.routes_geojson["features"][0]["geometry"]["coordinates"]
    assert [3.925, 6.8] not in coordinates
    assert any("excluded from trip distances" in w for w in result.warnings)
    _assert_rows_accounted_for(result)


def test_fix_after_a_glitch_is_not_blamed_for_it() -> None:
    jump = b"2026-09-15T08:05:00Z,6.8000,3.9250,18.0,V1\n"
    lines = STEADY.split(b"\n")
    content = HEADER + b"\n".join(lines[:3]) + b"\n" + jump + b"\n".join(lines[3:])
    result = analyse_csv(content, "jump.csv")
    assert [issue.row for issue in result.quality.issues] == [4]


def test_small_jitter_at_short_intervals_is_not_a_jump() -> None:
    # 100 m in 5 s is ~39 kn implied, but under the 250 m displacement floor.
    content = HEADER + (
        b"2026-09-15T08:00:00Z,6.5000,3.9000,1.0,V1\n"
        b"2026-09-15T08:00:05Z,6.5009,3.9000,1.0,V1\n"
        b"2026-09-15T08:00:10Z,6.5000,3.9000,1.0,V1\n"
    )
    result = analyse_csv(content, "jitter.csv")
    assert result.quality.flagged_by_reason == {}


def test_sustained_relocation_is_accepted_with_a_continuity_warning() -> None:
    # The track resumes 30 km away and stays there consistently.
    relocated = b"".join(
        f"2026-09-15T08:{10 + 2 * i:02d}:00Z,6.7700,{3.94 + 0.01 * i:.4f},18.0,V1\n".encode()
        for i in range(4)
    )
    result = analyse_csv(HEADER + STEADY + relocated, "relocated.csv")
    assert result.quality.excluded_from_geometry_rows == 0
    assert any("track discontinuities" in w for w in result.warnings)


# --- Malformed input ------------------------------------------------------------------


@pytest.mark.parametrize(
    "content,code",
    [
        (b"", "empty_file"),
        (b"   \n", "empty_file"),
        (HEADER, "empty_file"),
        (HEADER + b'2026-09-15T08:00:00Z,6.5,3.9,1,V1\n"unterminated,6.5\n', "malformed_csv"),
        (HEADER + "2026-09-15T08:00:00Z,6.5,3.9,1,Vé\n".encode("latin-1"), "malformed_csv"),
    ],
)
def test_unreadable_files_raise_coded_errors(content: bytes, code: str) -> None:
    with pytest.raises(MobilityDataError) as error:
        analyse_csv(content, "bad.csv")
    assert error.value.code == code


def test_row_limit_is_enforced() -> None:
    with pytest.raises(MobilityDataError) as error:
        analyse_csv(HEADER + STEADY, "big.csv", max_rows=3)
    assert error.value.code == "too_many_rows"


# --- Sample dataset with known injected anomalies -----------------------------------------


def test_sample_dataset_flags_exactly_the_injected_anomalies() -> None:
    result = analyse_csv(SAMPLE_CSV_PATH.read_bytes(), "sample.csv", data_status="simulated")
    assert result.provenance.input_rows == 992
    assert result.provenance.rejected_rows == 0
    assert result.quality.flagged_by_reason == {
        "position_jump": 1,
        "implausible_reported_speed": 1,
    }
    by_code = {issue.code: issue for issue in result.quality.issues}
    assert by_code["position_jump"].vessel_id == "EST-04"
    assert by_code["position_jump"].value == "6.7, 3.7"
    assert by_code["implausible_reported_speed"].vessel_id == "EST-02"
    assert by_code["implausible_reported_speed"].value == "65.0"
    # The jump previously inflated one trip to ~88 km; no trip should now be implausible.
    assert max(trip.distance_km for trip in result.trips) < 15
    assert max(trip.maximum_speed_knots for trip in result.trips) <= 40
    _assert_rows_accounted_for(result)
