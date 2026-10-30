from fastapi.testclient import TestClient

from estuara_api.main import app

from .test_analysis import VALID_CSV

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_upload_endpoint() -> None:
    response = client.post(
        "/api/v1/mobility/analyse",
        files={"file": ("pilot.csv", VALID_CSV, "text/csv")},
        data={"data_status": "simulated"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["trips"] == 2
    assert "landing_points" in body
    assert "landing_point_report" in body
    assert body["landing_point_report"]["algorithm"] == "DBSCAN"
    assert body["landing_points_geojson"]["type"] == "FeatureCollection"


def test_landing_point_parameters_are_configurable() -> None:
    response = client.post(
        "/api/v1/mobility/analyse",
        files={"file": ("pilot.csv", VALID_CSV, "text/csv")},
        data={
            "data_status": "simulated",
            "landing_radius_meters": "50",
            "landing_min_visits": "5",
        },
    )
    assert response.status_code == 200
    report = response.json()["landing_point_report"]
    assert report["radius_meters"] == 50
    assert report["min_visits"] == 5


def test_non_csv_upload_is_rejected() -> None:
    response = client.post(
        "/api/v1/mobility/analyse",
        files={"file": ("pilot.txt", VALID_CSV, "text/plain")},
    )
    assert response.status_code == 415



def test_non_csv_rejection_is_structured() -> None:
    response = client.post(
        "/api/v1/mobility/analyse",
        files={"file": ("pilot.txt", VALID_CSV, "text/plain")},
    )
    assert response.json()["detail"]["code"] == "unsupported_file"


def test_malformed_csv_is_distinguished_from_validation_failure() -> None:
    malformed = client.post(
        "/api/v1/mobility/analyse",
        files={"file": ("bad.csv", b"timestamp,lat\n\"broken,1\n", "text/csv")},
    )
    missing = client.post(
        "/api/v1/mobility/analyse",
        files={"file": ("cols.csv", b"timestamp,latitude\n2026-01-01T00:00:00Z,6.5\n", "text/csv")},
    )
    assert (malformed.status_code, malformed.json()["detail"]["code"]) == (422, "malformed_csv")
    assert (missing.status_code, missing.json()["detail"]["code"]) == (422, "missing_columns")


def test_invalid_parameter_returns_structured_error() -> None:
    response = client.post(
        "/api/v1/mobility/analyse",
        files={"file": ("pilot.csv", VALID_CSV, "text/csv")},
        data={"max_plausible_speed_knots": "5"},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_parameters"
    assert "max_plausible_speed_knots" in detail["message"]


def test_settings_are_echoed_in_the_response() -> None:
    response = client.post(
        "/api/v1/mobility/analyse",
        files={"file": ("pilot.csv", VALID_CSV, "text/csv")},
        data={"trip_gap_minutes": "45", "max_plausible_speed_knots": "30"},
    )
    settings = response.json()["settings"]
    assert settings["trip_gap_minutes"] == 45
    assert settings["max_plausible_speed_knots"] == 30
    assert "quality" in response.json()


def test_declared_oversized_upload_is_refused_before_reading(monkeypatch) -> None:
    monkeypatch.setenv("ESTUARA_MAX_UPLOAD_MB", "0.001")  # ~1 KB
    response = client.post(
        "/api/v1/mobility/analyse",
        files={"file": ("big.csv", VALID_CSV * 10, "text/csv")},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "file_too_large"


def test_row_limit_comes_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("ESTUARA_MAX_ROWS", "3")
    response = client.post(
        "/api/v1/mobility/analyse",
        files={"file": ("pilot.csv", VALID_CSV, "text/csv")},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "too_many_rows"


def test_health_reports_limits(monkeypatch) -> None:
    monkeypatch.setenv("ESTUARA_MAX_ROWS", "1234")
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["limits"]["max_rows"] == 1234


def test_cors_origins_come_from_environment(monkeypatch) -> None:
    from estuara_api.main import app as fresh_app

    monkeypatch.setenv("ESTUARA_ALLOWED_ORIGINS", "https://preview.example, http://localhost:3000")
    # Middleware resolves origins on first request; use a fresh stack.
    fresh_app.middleware_stack = None
    fresh = TestClient(fresh_app)
    allowed = fresh.get("/health", headers={"Origin": "https://preview.example"})
    denied = fresh.get("/health", headers={"Origin": "https://elsewhere.example"})
    assert allowed.headers.get("access-control-allow-origin") == "https://preview.example"
    assert "access-control-allow-origin" not in denied.headers
    fresh_app.middleware_stack = None
