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

